#     Copyright 2021, Kay Hayen, mailto:kay.hayen@gmail.com
#
#     Part of "Nuitka", an optimizing Python compiler that is compatible and
#     integrates with CPython, but also works on its own.
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
#
""" Standard plug-in to resolve pkg_resource actions at compile time rather than runtime.

Nuitka can detect some things that pkg_resouces may not even be able to during
runtime, e.g. right now checking pip installed versions, is not a thing, while
some packages in their code, e.g. derive their __version__ value from that.
"""


import re

from nuitka.plugins.PluginBase import NuitkaPluginBase


class NuitkaPluginResources(NuitkaPluginBase):
    plugin_name = "pkg-resources"
    plugin_desc = "Resolve version numbers at compile time."

    def __init__(self):
        try:
            import pkg_resources
        except (ImportError, RuntimeError):
            self.pkg_resources = None
        else:
            self.pkg_resources = pkg_resources

        try:
            import importlib_metadata
        except (ImportError, SyntaxError, RuntimeError):
            self.metadata = None
        else:
            self.metadata = importlib_metadata

        # Note: This one is overriding above import, but doesn't need to initialize
        # the value, since it will already be set in case of a problem.
        try:
            from importlib import metadata

            self.metadata = metadata
        except ImportError:
            pass

    @staticmethod
    def isAlwaysEnabled():
        return True

    def onModuleSourceCode(self, module_name, source_code):
        if self.pkg_resources:
            for match in re.findall(
                r"""\b(pkg_resources\.get_distribution\(\s*['"](.*?)['"]\s*\)\.((?:parsed_)?version))""",
                source_code,
            ):
                value = self.pkg_resources.get_distribution(match[1]).version

                if match[2] == "version":
                    value = repr(value)
                elif match[2] == "parsed_version":
                    value = "pkg_resources.extern.packaging.version.Version(%r)" % value
                else:
                    assert False

                source_code = source_code.replace(match[0], value)

            for match in re.findall(
                r"""\b(pkg_resources\.require\(\s*['"](.*?)['"]\s*\))""",
                source_code,
            ):
                # Explicitly call the require function at Nuitka compile, and
                # if it fails remove it so that it doesn't fail at execution
                try:
                    self.pkg_resources.require(match[1])
                except self.pkg_resources.ResolutionError:
                    raise self.pkg_resources.ResolutionError(
                        "Unmet requirement during compilation: "+match[1])
                else:
                    source_code = source_code.replace(match[0], "")

        if self.metadata:
            for match in re.findall(
                r"""\b((?:importlib_)?metadata\.version\(\s*['"](.*?)['"]\s*\))""",
                source_code,
            ):
                value = self.metadata.version(match[1])
                value = repr(value)

                source_code = source_code.replace(match[0], value)

        return source_code
