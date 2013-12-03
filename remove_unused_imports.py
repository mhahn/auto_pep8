#! /usr/bin/env python

from collections import defaultdict
import os
import re
import sys
from tempfile import mkstemp

from pyflakes import (
    api as pyflakes_api,
    reporter as pyflakes_reporter,
)


class RemoveUnusedImports(object):

    UNUSED_IMPORT_RE = r'.* imported but unused$'
    UNUSED_MODULE_NAME_RE = r"'(.*)'.*"

    SINGLE_IMPORT_RE = r'(from .*|^)import (.*as\s)?%s$'
    ESCAPE_CHARACTER = '\\'

    BASE_IMPORT_RE = r'(\s*)(from .*)?import .*'

    def __init__(self, base, commit_changes=False):
        self.base = base
        self.commit_changes = commit_changes

    @staticmethod
    def _clean_line(line):
        return re.sub(r'[\\\s]', '', line)

    def parse_pyflake_unused_import_error(self, error_line):
        path, line_num, error = error_line.split(':')
        regex_match = re.match(self.UNUSED_MODULE_NAME_RE, error.strip())
        unused_module = None
        if regex_match:
            unused_module = regex_match.groups()[0]
        return path, line_num, unused_module

    def get_unused_imports(self, directory):
        file_descriptor, absolute_path = mkstemp()
        with open(absolute_path, 'w') as report_output:
            eb_reporter = pyflakes_reporter.Reporter(report_output, sys.stderr)
            pyflakes_api.checkRecursive([directory], eb_reporter)

        with open(absolute_path, 'r') as report_output:
            unused_import_lines = []
            for line in report_output.xreadlines():
                if re.match(self.UNUSED_IMPORT_RE, line.strip()):
                    unused_import_lines.append(line)

        os.close(file_descriptor)
        os.remove(absolute_path)
        return unused_import_lines

    @staticmethod
    def get_modules_to_keep(imports, unused_imports):
        return sorted(list(set(imports) - set(unused_imports)))

    @staticmethod
    def split_single_line_multi_imports(line):
        imported_modules_string = line.split('import')[1].strip()
        imported_modules = map(
            lambda x: x.strip(),
            imported_modules_string.split(','),
        )
        return filter(None, imported_modules)

    @staticmethod
    def build_multiline_import(padding, base_import, imports):
        if not base_import:
            # import (
            #   module_a,
            #   module_b,
            # )
            # is invalid syntax
            output = 'import %s\n' % (', '.join(sorted(imports)))
        else:
            output = padding + base_import + 'import (\n'
            for _import in sorted(imports):
                output += '%s    %s,\n' % (padding, _import,)
            output += '%s)\n' % (padding,)
        return output

    def group_multiline_imports(self, start_index, file_lines):
        end_index = None
        current_index = start_index + 1
        group = []
        while end_index is None:
            current_line = file_lines[current_index].strip()
            if current_line == ')':
                end_index = current_index
                break
            group.append(current_line.strip(','))
            current_index += 1
        return group, end_index

    def group_escaped_imports(self, start_index, file_lines):
        end_index = None
        current_index = start_index + 1
        group = []
        imported_modules = self.split_single_line_multi_imports(
            self._clean_line(file_lines[start_index])
        )
        group.extend(imported_modules)
        while end_index is None:
            current_line = file_lines[current_index].strip()
            if self.ESCAPE_CHARACTER not in current_line:
                group.append(current_line.strip(','))
                end_index = current_index
                break
            group.append(self._clean_line(current_line).strip(','))
            current_index += 1
        return group, end_index

    def handle_single_line_multiple_imports(
            self,
            unused_imports,
            index,
            unused_import_line,
            line_adjustment,
            file_lines,
        ):
        """Handle multiple imports on a single line.

        ie:
            from package import module_a, module_c, module_b

        """
        if self.ESCAPE_CHARACTER in unused_import_line:
            imported_modules, end_index = self.group_escaped_imports(
                index,
                file_lines,
            )
            file_lines = file_lines[:index] + file_lines[end_index + 1:]
            line_adjustment += end_index - index
        else:
            imported_modules = (
                self.split_single_line_multi_imports(unused_import_line)
            )
            file_lines.pop(index)

        modules_to_keep = self.get_modules_to_keep(
            imported_modules,
            unused_imports,
        )
        if modules_to_keep:
            base_import_match = re.match(
                self.BASE_IMPORT_RE,
                unused_import_line,
            )
            padding, base_import = base_import_match.groups('')
            if len(modules_to_keep) > 1:
                new_line = self.build_multiline_import(
                    padding,
                    base_import,
                    modules_to_keep,
                )
            else:
                new_line = (
                    padding + base_import + 'import %s\n' % tuple(
                        modules_to_keep
                    )
                )
            file_lines.insert(index, new_line)
        else:
            line_adjustment += 1
        return line_adjustment, file_lines

    def handle_multiline_imports(
            self,
            unused_imports,
            index,
            unused_import_line,
            line_adjustment,
            file_lines,
        ):
        """Handle multiline import statements.

        ie:
            from package import (
                module_a,
                module_b,
                module_c,
            )

        """
        imported_modules, end_index = self.group_multiline_imports(
            index,
            file_lines,
        )
        modules_to_keep = self.get_modules_to_keep(
            imported_modules,
            unused_imports,
        )

        old_length = len(file_lines)
        file_lines = file_lines[:index] + file_lines[end_index + 1:]
        if not modules_to_keep:
            pass
        elif len(modules_to_keep) == 1:
            unused_import_line = unused_import_line.strip('(\n')
            file_lines.insert(
                index,
                '%s%s\n' % (unused_import_line, modules_to_keep[0]),
            )
        else:
            base_import_match = re.match(
                self.BASE_IMPORT_RE,
                unused_import_line,
            )
            padding, base_import = base_import_match.groups('')
            new_line = self.build_multiline_import(
                padding,
                base_import,
                modules_to_keep,
            )
            file_lines.insert(index, new_line)
        line_adjustment += old_length - len(file_lines)
        return line_adjustment, file_lines

    def handle_single_line_imports(self, index, line_adjustment, file_lines):
        """Handle single line imports.

        ie:
            from package import module
            import package

        """
        file_lines.pop(index)
        line_adjustment += 1
        return line_adjustment, file_lines

    def remove_unused_imports_from_file(self, file_path, unused_imports):
        if file_path.endswith('__init__.py'):
            print 'ignoring __init__.py file:', file_path
            return

        print 'removing unused imports from file:', file_path
        with open(file_path, 'r') as read_file:
            file_lines = read_file.readlines()

        # group imports by line so we can handle mutliline imports all at once
        unused_imports_by_line = defaultdict(list)
        for line_num, unused_module in unused_imports:
            unused_imports_by_line[int(line_num)].append(unused_module)

        line_adjustment = 0
        for line_num in sorted(unused_imports_by_line.keys()):
            unused_imports = unused_imports_by_line[line_num]
            index = line_num - 1 - line_adjustment
            unused_import_line = file_lines[index]

            if len(unused_import_line.split(',')) > 1:
                line_adjustment, file_lines = (
                    self.handle_single_line_multiple_imports(
                        unused_imports,
                        index,
                        unused_import_line,
                        line_adjustment,
                        file_lines,
                    )
                )
            elif unused_import_line.endswith('(\n'):
                line_adjustment, file_lines = self.handle_multiline_imports(
                    unused_imports,
                    index,
                    unused_import_line,
                    line_adjustment,
                    file_lines,
                )
            elif re.match(
                self.SINGLE_IMPORT_RE % (unused_imports[0],),
                unused_import_line.strip()
            ):
                line_adjustment, file_lines = self.handle_single_line_imports(
                    index,
                    line_adjustment,
                    file_lines,
                )

        if self.commit_changes:
            with open(file_path, 'w') as write_file:
                write_file.writelines(file_lines)

    def remove_unused_imports(self, unused_imports):
        # group errors by file
        files_to_clean = defaultdict(list)
        for unused_import_error in unused_imports:
            path, line_num, unused_module = (
                self.parse_pyflake_unused_import_error(unused_import_error)
            )
            files_to_clean[path].append((line_num, unused_module))
        for file_path, unused_imports in files_to_clean.iteritems():
            self.remove_unused_imports_from_file(file_path, unused_imports)

    def process(self):
        unused_imports = self.get_unused_imports(self.base)
        return self.remove_unused_imports(unused_imports)


if __name__ == '__main__':
    remover = RemoveUnusedImports(sys.argv[1], commit_changes=int(sys.argv[2]))
    remover.process()
