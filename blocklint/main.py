from __future__ import print_function
import re
import sys
import argparse
import configparser
import os
from collections import OrderedDict
if sys.version_info >= (3, 5):
    from typing import Dict, Set, Union


ignore_class = '[^a-zA-Z0-9]'

# TODO fix broken pipe error
# https://pycodestyle.pycqa.org/en/latest/intro.html#configuration
# try:
#     if sys.platform == 'win32':
#         USER_CONFIG = os.path.expanduser(r'~\.pycodestyle')
#     else:
#         USER_CONFIG = os.path.join(
#             os.getenv('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'),
#             'pycodestyle'
#         )
# except ImportError:
#     USER_CONFIG = None


def main(args=None):
    args = get_args(args)
    word_checkers = generate_re(args)
    total_issues = 0

    if args['stdin']:
        total_issues += process_file(sys.stdin, 'stdin', word_checkers,
                                     args['end_pos'])
    else:
        for file in args['files']:
            with open(file, 'r') as handle:
                total_issues += process_file(handle, file, word_checkers,
                                             args['end_pos'])

    if (args['max_issue_threshold'] is not None
            and args['max_issue_threshold'] <= total_issues):
        print(("Found {issues} issues, with maximum set to "
               "{max}!").format(
                   issues=total_issues,
                   max=args['max_issue_threshold']))
        sys.exit(1)


def process_file(input_file, file_name, word_checkers, end_pos):
    num_matched = 0
    try:
        for i, line in enumerate(input_file, 1):
            for match in check_line(line, word_checkers,
                                    file_name, i, end_pos):
                num_matched += 1
                print(match)
    except FileNotFoundError:
        pass
    except UnicodeDecodeError:
        pass
    return num_matched


def get_args(args=None):
    parser = argparse.ArgumentParser(description='Lint block-listed words')
    parser.add_argument('files', nargs='*',
                        help='Files or directories to lint, default all '
                        'files in current directory')
    parser.add_argument('--blocklist', help='Comma separated list of words '
                        'to lint in any context, with possibly special '
                        'characters between, case insensitive; '
                        'DEFAULT to master,slave,whitelist,blacklist')
    parser.add_argument('--wordlist', help='Comma separated list of words '
                        'to lint as whole words, with possibly special '
                        'characters between, case insensitive')
    parser.add_argument('--exactlist', help='Comma separated list of words '
                        'to lint as whole words exactly as entered')
    parser.add_argument('-e', '--end-pos', action='store_true',
                        help='Show the end position of a match in output')
    parser.add_argument('--stdin', action='store_true',
                        help='Use stdin as input instead of file list')
    parser.add_argument("--max-issue-threshold", type=int, required=False,
                        help='Cause non-zero exit status of more than this '
                        'many issues found')
    parser.add_argument("--skip-files", type=str,
                        help='Paths to files that should _not_ be checked by '
                        'blocklint, even if within a checked directory')
    args = vars(parser.parse_args(args))

    config_paths = [
        os.path.join(os.path.expanduser('~'), '.blocklint'),
        './.blocklint',
        './setup.cfg',
        './tox.ini',
    ]
    present_config_files = [
        path for path in config_paths if os.path.exists(path)
    ]
    config = configparser.ConfigParser()
    for path in present_config_files:
        config.read(path)
    config_settings = {}  # type: Dict[str, Union[str, bool, int, Set[str]]]
    if 'blocklint' in config:
        config_settings = dict(config['blocklint'])
    for key in args:
        if args[key] is None and key in config_settings:
            if key in ['end_pos', 'stdin']:
                config_settings[key] = config.getboolean('blocklint', key)
            if key in ['max_issue_threshold']:
                config_settings[key] = config.getint('blocklint', key)
            if key in ['skip_files']:
                config_settings[key] = config.get('blocklint', key)
            args[key] = config_settings[key]
    if args['skip_files'] is not None:
        # config files have multiline args
        skip_files = args['skip_files'].split('\n')
        skip_files = [path for line in skip_files
                      for path in line.split(',')]
        args['skip_files'] = set(skip_files)

    # from least to most restrictive
    wordlists = ('blocklist', 'wordlist', 'exactlist')

    # TODO add in checks for config files
    if args['blocklist'] is None and \
            args['wordlist'] is None and \
            args['exactlist'] is None:
        args['blocklist'] = 'master,slave,whitelist,blacklist'

    for wordlist in wordlists:
        if args[wordlist] is not None:
            # split CSV, remove duplicates
            args[wordlist] = set(args[wordlist].split(','))
        else:
            args[wordlist] = set()

    # remove repeats across lists from least to most restrictive
    for i, wordlist in enumerate(wordlists):
        for other in wordlists[i+1:]:
            args[other] -= args[wordlist]

        # sort for deterministic output
        args[wordlist] = sorted(args[wordlist])

    # parse files argument into individual files
    if not args['files']:
        args['files'] = [os.getcwd()]

    files = []
    for file in args['files']:
        if os.path.isdir(file):
            files += [os.path.join(file, f) for f in os.listdir(file)
                      if os.path.isfile(os.path.join(file, f))]
        # isabs detects pipes
        elif os.path.isfile(file) or os.path.isabs(file):
            files.append(file)
    if args['skip_files'] is not None:
        files = [file for file in files if file not in args['skip_files']]

    args['files'] = files

    return args


def generate_re(args):
    result = OrderedDict()

    for word in args['blocklist']:
        result[word] = re.compile(ignore_special(word), re.IGNORECASE)

    for word in args['wordlist']:
        result[word] = re.compile(word_boundaries(ignore_special(word)),
                                  re.IGNORECASE)

    for word in args['exactlist']:
        result[word] = re.compile(word_boundaries(re.escape(word)))

    return result


def ignore_special(input_pattern):
    return (ignore_class + '?').join(re.escape(char) for char in input_pattern)


def word_boundaries(input_pattern):
    if input_pattern:
        input_pattern = r'\b' + input_pattern + r'\b'
    return input_pattern


def check_line(line, word_checkers, file, line_number, end_pos=False):
    fmt_str = '{file}:{line_number}:{start}: use of "{word}"'
    if end_pos:
        fmt_str = '{file}:{line_number}:{start}:{end}: use of "{word}"'

    pragma_regex = re.compile(r"blocklint:.*pragma")
    if pragma_regex.search(line):
        return

    for word, regex in word_checkers.items():
        for match in regex.finditer(line):
            yield fmt_str.format(
                file=file,
                line_number=line_number,
                start=match.start()+1,
                end=match.end(),
                word=word)


if __name__ == '__main__':
    main()
