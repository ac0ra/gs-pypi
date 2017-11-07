#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
    pypi_db.py
    ~~~~~~~~~~

    PyPI package database

    :copyright: (c) 2013-2015 by Jauhien Piatlicki
    :license: GPL-2, see LICENSE for more details.
"""

import datetime
import re
import time
import glob
import os
import requests
import urllib

import bs4
import multiprocessing
import tarfile

from g_sorcery.fileutils import _call_parser, wget, load_remote_file
from g_sorcery.compatibility import TemporaryDirectory
from g_sorcery.db_layout import BSON_FILE_SUFFIX
from g_sorcery.exceptions import DownloadingError
from g_sorcery.g_collections import Package, serializable_elist
from g_sorcery.package_db import DBGenerator, PackageDB

def pypi_versions(name):
    url = "https://pypi.python.org/pypi/{}/json".format(name)
    versions = []
    try:
        releases = requests.get(url).json()["releases"]
    except:
        releases = []
    for ver in releases:
        versions.append(ver)
    return versions


def _call_parser(f_name, parser, open_file = True, open_mode = 'r'):
    """
    Call parser on a given file.

    Args:
        f_name: File name.
        parser: Parser function.
        open_file: Whether parser accepts a file descriptor.
        open_mode: Open mode for a file.

    Returns:
        A dictionary with one entry. Key if a file name, content is
    content returned by parser.
    """
    data = None
    if isinstance(parser, basestring):
        parser = getattr(PypiDBGenerator, parser)
    if open_file:
        with open(f_name, open_mode) as f:
            data = parser(f)
    else:
        data = parser(f_name)
    return {os.path.basename(f_name): data}


def load_remote_file(uri, parser, open_file = True, open_mode = 'r', output = "", timeout = None):
    """
    Load files from an URI.

    Args:
        uri: URI.
        parser: Parser that will be applied to downloaded files.
        open_file: Whether parser accepts a file descriptor.
        open_mode: Open mode for a file.
        output: What output name should downloaded file have.
        timeout: URI access timeout.
    (it will be a key identifying data loaded from this file)

    Returns:
        Dictionary with a loaded data. Key is filename, content is data returned by parser.
    """
    download_dir = TemporaryDirectory()
    loaded_data = {}
    #if wget(uri, download_dir.name, output, timeout=timeout):
    url = urllib.URLopener()
    if not url.retrieve(uri,filename=download_dir.name+"/"+output):
        raise DownloadingError("wget failed: " + uri)
    for f_name in glob.glob(os.path.join(download_dir.name, "*")):
        if tarfile.is_tarfile(f_name):
            unpack_dir = TemporaryDirectory()
            with tarfile.open(f_name) as f:
                f.extractall(unpack_dir.name)
            for uf_name in glob.glob(os.path.join(unpack_dir, "*")):
                loaded_data.update(_call_parser(uf_name, parser,
                                    open_file=open_file, open_mode=open_mode))
            del unpack_dir
        else:
            name, extention = os.path.splitext(f_name)
            if extention in [".xz", ".lzma"]:
                if (os.system("xz -d " + f_name)):
                    raise DownloadingError("xz failed: "
                                + f_name + " from " + uri)
                f_name = name
            loaded_data.update(_call_parser(f_name, parser,
                                open_file=open_file, open_mode=open_mode))
    del download_dir
    return loaded_data


class Worker(multiprocessing.Process):
    def __init__(self, task_queue, result_queue):
        multiprocessing.Process.__init__(self)
        self.task_queue = task_queue
        self.result_queue = result_queue

    def run(self):
        proc_name = self.name
        while True:
            uri = self.task_queue.get()
            if uri is None:
                print( '%s: Exiting' % proc_name)
                self.task_queue.task_done()
                break
            attempts = 0
            while True:
                    try:
                        attempts += 1
                        data = load_remote_file(**uri)
                    except DownloadingError as error:
                        print(str(error))
                        if attempts < 100:
                            continue
                        elif attempts == 100:
                            self.task_queue.task_done()
                            break
                    self.task_queue.task_done()
                    self.result_queue.put(data)
                    for n in data:
                        print(n)
                    break
            #print(data)


class PypiDBGenerator(DBGenerator):
    """
    Implementation of database generator for PYPI backend.
    """

    def __init__(self, package_db_class=PackageDB,
                 preferred_layout_version=1,
                 preferred_db_version=1,
                 preferred_category_format=BSON_FILE_SUFFIX,
                 count=None):
        super(PypiDBGenerator, self).__init__(package_db_class=package_db_class,
                                              preferred_layout_version=preferred_layout_version,
                                              preferred_db_version=preferred_db_version,
                                              preferred_category_format=preferred_category_format)
        self.count = count

    def generate_tree(self, pkg_db, common_config, config):
        """
        Generate package entries.

        Args:
            pkg_db: Package database.
            common_config: Backend config.
            config: Repository config.
        """
        ## MULTIPROCESSING
        self.first = True
        self.lock = multiprocessing.Lock()
        self.task_queue = multiprocessing.JoinableQueue()
        #self.task_queue = multiprocessing.Queue()
        self.result_queue = multiprocessing.Queue()
        self.nproc = multiprocessing.cpu_count() * 2
        workers = [ Worker(self.task_queue, self.result_queue) for n in xrange(self.nproc)]
        for w in workers:
            w.start()
        data = self.download_data(common_config, config)
        for n in xrange(self.nproc):
            self.task_queue.put(None)
        self.process_data(pkg_db, data, common_config, config)

    def download_data(self, common_config, config):
        """
        Obtain data for database generation.

        Args:
            common_config: Backend config.
            config: Repository config.

        Returns:
            Downloaded data.
        """
        uries = self.get_download_uries(common_config, config)
        uries = self.decode_download_uries(uries)
        data = {}
        for uri in uries:
            self.process_uri(uri, data)
        return data

    def decode_download_uries(self, uries):
        """
        Convert URI list with incomplete and string entries
        into list with complete dictionary entries.

        Args:
            uries: List of URIes.

        Returns:
            List of URIes with dictionary entries.
        """
        decoded = []
        for uri in uries:
            decuri = {}
            if isinstance(uri, basestring):
                decuri["uri"] = uri
                decuri["parser"] = self.parse_data
                decuri["open_file"] = True
                decuri["open_mode"] = "r"
            else:
                decuri = uri
                if not "parser" in decuri:
                    decuri["parser"] = self.parse_data
                if not "open_file" in decuri:
                    decuri["open_file"] = True
                if not "open_mode" in decuri:
                    decuri["open_mode"] = "r"
            decoded.append(decuri)
        return decoded

    def get_download_uries(self, common_config, config):
        """
        Get URI of packages index.
        """
        self.repo_uri = config["repo_uri"]
        return [{"uri": self.repo_uri + "simple", "output": "packages"}]

    def parse_data(self, data_f):
        """
        Download and parse packages index. Then download and parse pages for all packages.
        """
        soup = bs4.BeautifulSoup(data_f, "lxml")
        packages = soup.body
        data = {}
        data["index"] = {}
        ## MULTIPROCESSING
        task_queue = self.task_queue
        result_queue = self.result_queue

        pkg_uries = []

        last = -1
        if self.count:
            last = self.count
        if packages is not None:
            for entry in packages.find_all("a"):
                package = entry.get_text()
                versions = pypi_versions(package)

                for version in versions:
                    description = ""
                    data["index"][(package, version)] = description
                    pkg_job = {"uri": self.repo_uri + "pypi/" + package + "/" + version,
                                    "parser": "parse_package_page",
                                    "output": package + "-" + version,
                                    "timeout": 2}
                    task_queue.put(pkg_job)
                    entry.decompose()
            packages.decompose
        elif packages is None:
            print("Error: I have returned no packages??")
        soup.decompose()
        pkg_uries = self.decode_download_uries(pkg_uries)



        task_queue.join()
        if self.first is True:
            time.sleep(5)
            self.first = False
            data.update()
        while result_queue.empty() is not True:
            time.sleep(1)
            print("Emptying Queue: estimated queue length left {}".format(str(result_queue.qsize())))
            data.update(result_queue.get())
            if self.first is True:
                print(data)
        print(data)
        return data


    @staticmethod
    def parse_package_page(data_f):
        """
        Parse package page.
        """
        soup = bs4.BeautifulSoup(data_f.read(), "lxml")
        data = {}
        data["files"] = []
        data["info"] = {}
        try:
            for table in soup("table", class_ = "list")[-1:]:
                if not "File" in table("th")[0].string:
                    continue

                for entry in table("tr")[1:-1]:
                    fields = entry("td")

                    FILE = 0
                    URL = 0
                    MD5 = 1

                    TYPE = 1
                    PYVERSION = 2
                    UPLOADED = 3
                    SIZE = 4

                    file_inf = fields[FILE]("a")[0]["href"].split("#")
                    file_url = file_inf[URL]
                    file_md5 = file_inf[MD5][4:]

                    file_type = fields[TYPE].string
                    file_pyversion = fields[PYVERSION].string
                    file_uploaded = fields[UPLOADED].string
                    file_size = fields[SIZE].string

                    data["files"].append({"url": file_url,
                                          "md5": file_md5,
                                          "type": file_type,
                                          "pyversion": file_pyversion,
                                          "uploaded": file_uploaded,
                                          "size": file_size})
                    entry.decompose()
                table.decompose()

            uls = soup("ul", class_ = "nodot")
            if uls:
                if "Downloads (All Versions):" in uls[0]("strong")[0].string:
                    ul = uls[1]
                else:
                    ul = uls[0]

                for entry in ul.contents:
                    if not hasattr(entry, "name") or entry.name != "li":
                        continue
                    entry_name = entry("strong")[0].string
                    if not entry_name:
                        continue

                    if entry_name == "Categories":
                        data["info"][entry_name] = {}
                        for cat_entry in entry("a"):
                            cat_data = cat_entry.string.split(" :: ")
                            if not cat_data[0] in data["info"][entry_name]:
                                data["info"][entry_name][cat_data[0]] = cat_data[1:]
                            else:
                                data["info"][entry_name][cat_data[0]].extend(cat_data[1:])
                        continue

                    if entry("span"):
                        data["info"][entry_name] = entry("span")[0].string
                        continue

                    if entry("a"):
                        data["info"][entry_name] = entry("a")[0]["href"]
                        continue
                    entry.decompose()
                ul.decompose()

        except Exception as error:
            print("There was an error during parsing: " + str(error))
            print("Ignoring this package.")
            data = {}
            data["files"] = []
            data["info"] = {}

        soup.decompose()
        return data

    def process_data(self, pkg_db, data, common_config, config):
        """
        Process parsed package data.
        """
        category = "dev-python"
        pkg_db.add_category(category)

        common_data = {}
        common_data["eclasses"] = ['g-sorcery', 'gs-pypi']
        common_data["maintainer"] = [{'email' : 'jauhien@gentoo.org',
                                      'name' : 'Jauhien Piatlicki'}]
        common_data["dependencies"] = serializable_elist(separator="\n\t")
        pkg_db.set_common_data(category, common_data)

        #todo: write filter functions
        allowed_ords_pkg = set(range(ord('a'), ord('z') + 1)) | set(range(ord('A'), ord('Z') + 1)) | \
            set(range(ord('0'), ord('9') + 1)) | set(list(map(ord,
                ['+', '_', '-'])))

        allowed_ords_desc = set(range(ord('a'), ord('z') + 1)) | set(range(ord('A'), ord('Z') + 1)) | \
              set(range(ord('0'), ord('9') + 1)) | set(list(map(ord,
                    ['+', '_', '-', ' ', '.', '(', ')', '[', ']', '{', '}', ','])))

        now = datetime.datetime.now()
        pseudoversion = "%04d%02d%02d" % (now.year, now.month, now.day)

        for (package, version), description in data["packages"]["index"].items():

            pkg = package + "-" + version
            if not pkg in data["packages"]:
                continue

            pkg_data = data["packages"][pkg]

            if not pkg_data["files"] and not pkg_data["info"]:
                continue

            files_src_uri = ""
            md5 = ""
            if pkg_data["files"]:
                for file_entry in pkg_data["files"]:
                    if file_entry["type"] == "\n    Source\n  ":
                        files_src_uri = file_entry["url"]
                        md5 = file_entry["md5"]
                        break

            download_url = ""
            info = pkg_data["info"]
            if info:
                if "Download URL:" in info:
                    download_url = info["Download URL:"]

            if download_url:
                source_uri = download_url #todo: find how to define src_uri
            else:
                source_uri = files_src_uri

            if not source_uri:
                continue

            homepage = ""
            pkg_license = ""
            py_versions = []
            if info:
                if "Home Page:" in info:
                    homepage = info["Home Page:"]
                categories = {}
                if "Categories" in info:
                    categories = info["Categories"]

                    if 'Programming Language' in  categories:
                        for entry in categories['Programming Language']:
                            if entry == '2':
                                py_versions.extend(['2_7'])
                            elif entry == '3':
                                py_versions.extend(['3_3', '3_4', '3_5'])
                            elif entry == '2.6':
                                py_versions.extend(['2_7'])
                            elif entry == '2.7':
                                py_versions.extend(['2_7'])
                            elif entry == '3.2':
                                py_versions.extend(['3_3'])
                            elif entry == '3.3':
                                py_versions.extend(['3_3'])
                            elif entry == '3.4':
                                py_versions.extend(['3_4'])
                            elif entry == '3.5':
                                py_versions.extend(['3_5'])


                    if "License" in categories:
                        pkg_license = categories["License"][-1]
            pkg_license = self.convert([common_config, config], "licenses", pkg_license)

            if not py_versions:
                py_versions = ['2_7', '3_3', '3_4', '3_5']

            if len(py_versions) == 1:
                python_compat = '( python' + py_versions[0] + ' )'
            else:
                python_compat = '( python{' + py_versions[0]
                for ver in py_versions[1:]:
                    python_compat += ',' + ver
                python_compat += '} )'

            filtered_package = "".join([x for x in package if ord(x) in allowed_ords_pkg])
            description = "".join([x for x in description if ord(x) in allowed_ords_desc])
            filtered_version = version
            match_object = re.match("(^[0-9]+[a-z]?$)|(^[0-9][0-9\.]+[0-9][a-z]?$)",
                                    filtered_version)
            if not match_object:
                filtered_version = pseudoversion

            ebuild_data = {}
            ebuild_data["realname"] = package
            ebuild_data["realversion"] = version

            ebuild_data["description"] = description
            ebuild_data["longdescription"] = description

            ebuild_data["homepage"] = homepage
            ebuild_data["license"] = pkg_license
            ebuild_data["source_uri"] = source_uri
            ebuild_data["md5"] = md5
            ebuild_data["python_compat"] = python_compat

            pkg_db.add_package(Package(category, filtered_package, filtered_version), ebuild_data)
