import os
import re
import unittest
from tempfile import mkstemp

from remove_unused_imports import RemoveUnusedImports


class _BaseTestCase(unittest.TestCase):

    fixture = None
    expected_result = None

    fixtures_path = os.path.join(os.path.dirname(__file__), 'fixtures')

    def __init__(self, *args, **kwargs):
        if self.fixture is None:
            raise NotImplementedError('TestCase must specify fixture')

        if self.expected_result is None:
            raise NotImplementedError('TestCase must define expected result')

        super(_BaseTestCase, self).__init__(*args, **kwargs)

    def tearDown(self):
        os.close(self.file_descriptor)
        os.remove(self.temp_path)

    def test_remove_unused_imports(self):
        fixture_path = os.path.join(self.fixtures_path, self.fixture)
        expected_fixture_path = os.path.join(
            self.fixtures_path,
            self.expected_result,
        )
        self.file_descriptor, self.temp_path = mkstemp()
        with open(fixture_path, 'r') as read_fixture:
            with open(self.temp_path, 'w') as write_temp:
                write_temp.writelines(read_fixture.readlines())

        remover = RemoveUnusedImports(self.temp_path, commit_changes=True)
        clean_lines = remover.process()

        with open(expected_fixture_path, 'r') as expected_fixture:
            with open(self.temp_path, 'r') as read_temp:
                result = read_temp.readlines()
                expected = expected_fixture.readlines()
                self.assertEqual(expected, result)


class TestSingleLineImports(_BaseTestCase):

    fixture = 'single_line_imports.py'
    expected_result = 'single_line_imports_expected.py'


class TestSignleLineMultipleImports(_BaseTestCase):

    fixture = 'single_line_multiple_imports.py'
    expected_result = 'single_line_multiple_imports_expected.py'


class TestMultilineImports(_BaseTestCase):

    fixture = 'multiline_imports.py'
    expected_result = 'multiline_imports_expected.py'


class TestRemoveUnusedImports(unittest.TestCase):

    def setUp(self):
        self.remover = RemoveUnusedImports(None)

    def test_parse_pyflake_unused_import_error(self):
        path, line_num, unused_module = (
            self.remover.parse_pyflake_unused_import_error(
                "fixtures/multiline_imports.py:1: 'chown' imported but unused"
            )
        )
        self.assertEqual(path, 'fixtures/multiline_imports.py')
        self.assertEqual(line_num, '1')
        self.assertEqual(unused_module, 'chown')

    def test_get_modules_to_keep(self):
        imports = ['module_b', 'module_c', 'module_a', 'module_d']
        unused_imports = ['module_b', 'module_d']
        modules_to_keep = self.remover.get_modules_to_keep(
            imports,
            unused_imports,
        )
        self.assertEqual(
            modules_to_keep,
            ['module_a', 'module_c'],
        )

    def test_group_multiline_imports(self):
        file_lines = [
            'import module\n',
            'from package import (\n',
            '   module_a,\n',
            '   module_b,\n',
            '   module_c,\n',
            '   module_d\n',
            ')\n',
            '\n',
            'import module_e\n',
        ]
        group, end_index = self.remover.group_multiline_imports(1, file_lines)
        self.assertEqual(end_index, 6)
        self.assertEqual(
            group,
            ['module_a', 'module_b', 'module_c', 'module_d'],
        )

    def test_group_escaped_imports(self):
        file_lines = [
            'import module\n',
            'from package import module_a, module_b,\\\n',
            '   module_c\n',
            '\n'
            'import module_d\n',
        ]
        group, end_index = self.remover.group_escaped_imports(1, file_lines)
        self.assertEqual(end_index, 2)
        self.assertEqual(
            group,
            ['module_a', 'module_b', 'module_c'],
        )

    def test_group_escaped_imports_multiple(self):
        file_lines = [
            'from package import module_a, \\\n',
            '   module_b, \\\n',
            '   module_c, \\\n',
            '   module_d\n',
        ]
        group, end_index = self.remover.group_escaped_imports(0, file_lines)
        self.assertEqual(end_index, 3)
        self.assertEqual(
            group,
            ['module_a', 'module_b', 'module_c', 'module_d'],
        )

    def test_split_single_line_multi_imports(self):
        single_line_multi_imports = 'from package import module_a, module_b'
        imported_modules = (
            self.remover.split_single_line_multi_imports(
                single_line_multi_imports
            )
        )
        self.assertEqual(imported_modules, ['module_a', 'module_b'])

    def test_build_multiline_import(self):
        multiline_import = self.remover.build_multiline_import(
            '',
            'from package ',
            ['module_a', 'module_c', 'module_b'],
        )
        self.assertEqual(
            multiline_import,
            'from package import (\n'
            '    module_a,\n'
            '    module_b,\n'
            '    module_c,\n'
            ')\n'
        )

    def test_build_multiline_import_padding(self):
        multiline_import = self.remover.build_multiline_import(
            '    ',
            'from package ',
            ['module_a', 'module_c', 'module_b'],
        )
        self.assertEqual(
            multiline_import,
            '    from package import (\n'
            '        module_a,\n'
            '        module_b,\n'
            '        module_c,\n'
            '    )\n'
        )

    def test_base_import_re_invalid(self):
        self.assertFalse(
            re.match(
                self.remover.BASE_IMPORT_RE,
                'random invalid string',
            )
        )

    def test_base_import_re_from_basic(self):
        self.assertTrue(
            re.match(
                self.remover.BASE_IMPORT_RE,
                'from package import module',
            ),
        )

    def test_base_import_re_from_submodule(self):
        self.assertTrue(
            re.match(
                self.remover.BASE_IMPORT_RE,
                'from package.subpackage import module',
            ),
        )

    def test_base_import_re_basic(self):
        self.assertTrue(
            re.match(
                self.remover.BASE_IMPORT_RE,
                'import module_a, module_b',
            ),
        )

    def test_base_import_re_from_padding(self):
        match = re.match(
                self.remover.BASE_IMPORT_RE,
                '    from package.subpackage import module',
        )
        self.assertTrue(match)
        self.assertEqual(match.groups()[0], '    ')
        self.assertEqual(match.groups()[1], 'from package.subpackage ')

    def test_base_import_re_basic_padding(self):
        match = re.match(
            self.remover.BASE_IMPORT_RE,
            '    import module_a, module_b',
        )
        self.assertTrue(match)
        self.assertEqual(match.groups()[0], '    ')
        self.assertFalse(match.groups()[1])
