#!/usr/bin/env python
#
# Copyright (c) 2012-2021 Chris Rees. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
# OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE PROJECT BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
# NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
# THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
#

MAJOR = 1
MINOR = 3
MICRO = 0

DATADIR = '.'

import argparse
import logging
import subprocess
import re
import sys
import textwrap


class Db:
    def __init__(self, dbname, language):
        self._contents = []
        with open('%s/%s.%s' % (DATADIR, dbname, language)) as f:
            logging.debug('Sucking in %s database' % dbname)
            for e in f.readlines():
                e = e.rstrip('\n')
                if not e or e[0] == '#':
                    continue
                self._contents.append(e.split('\t'))
        # Count the errors and bail if too many
        self.count = 0

    def _get(self, key, index):
        for c in self._contents:
            if c[0] == key:
                return c[index]
        return False

    def error(self, key):
        return self._get(key, 1)

    def explanation(self, key):
        return self._get(key, 2)

    def give(self, key, num=-1, level='error'):
        err = self.error(key)
        if err:
            if level == 'critical':
                logging.critical('[%d]: %s ' % (num+1, err))
                exit()
            if level == 'error':
                logging.error('[%d]: %s ' % (num+1, err))
            if level == 'warn':
                logging.warning('[%d]: %s ' % (num+1, err))
            if verbosity > 0:
                print((textwrap.fill(self.explanation(key),
                                    initial_indent='==> ',
                                    subsequent_indent='    ')))
        else:
            logging.error('No such error: %s' % key)
        self.count += 1
        if self.count > 10 and not beaucoup_errors:
            hint = '  Try rerunning with -v option for extra details.' if verbosity == 0 else ''
            logging.error('Error threshold reached-- further errors are unlikely to be helpful.  Fix the errors and rerun.  The -k option will cause rclint to continue for as many errors as it finds.' + hint)
            exit()

    def warn(self, key, num=-1, level='warn'):
        self.give(key, num, level)

    def critical(self, key, num=-1):
        self.give(key, num, 'critical')

class Statement:
    def __init__(self, lines, number):
        types = {'.': 'source', 'load_rc_config': 'load_rc_config',
                 'run_rc_command': 'run_rc_command'}
        self.length = 1
        spl = lines[number].split(' ')
        if spl[0] in types:
            self.name = spl[0]
            self.type = types[spl[0]]
            self.value = ' '.join(spl[1:])
            while self.value[-1] == '\\':
                self.value = ' '.join((self.value[:-1], lines[number+self.length]))
                self.length += 1
            self.line = number
        else:
            self.value = False

    def quoted(self):
        if self.value and (self.value[0] == '"' or self.value[0] == '\''):
            return True
        else:
            return False

    def pointless_quoted(self):
        if not self.quoted():
            return False
        for char in self.value[1:-1]:
            if char in ' \t|%&;<>()$`\\\"\'':
                return False
        return True

    def get_value(self):
        if self.quoted():
            return self.value[1:-1]
        else:
            return self.value


class Variable(Statement):
    def __init__(self, lines, number):
        line = lines[number]
        self.length = 1
        basic = re.compile(r'^([^#\s=]+)=(.*)')
        result = basic.match(line)
        if result:
            is_longhand = self.is_longhand_default(line)
            if is_longhand:
                (self.name, self.source, colon, self.value) = is_longhand
                self.clobber = True if colon[0] == ':' else False
                self.type = 'longhand'
            else:
                (self.name, self.value) = result.groups()
                while len(self.value) > 0 and self.value[-1] == '\\':
                    self.value = ' '.join((self.value[:-1],
                                           lines[number+self.length]))
                    self.length += 1
                self.type = (
                    'init' if self.name in ('name', 'desc', 'rcvar') else 'basic')

        elif line[:4] == 'eval':
            self.value = line
            while self.value[-1] == '\\':
                self.value = ' '.join(self.value[:-1],
                                      lines[number+self.length])
                self.length += 1
            self.name = line
            self.type = 'eval'
        else:
            is_shorthand = self.is_shorthand_default(line)
            if is_shorthand:
                (self.name, assignment, self.value) = is_shorthand
                self.clobber = True if assignment[0] == ':' else False
                self.type = 'shorthand'

        if not hasattr(self, 'value'):
            self.value = False
        self.line = number

    def is_longhand_default(self, line):
        match = re.match(r'([^=\s]+)=\${(^[:-]+)(:?-)([^}]+)', line)
        return match.groups() if match else False

    def is_shorthand_default(self, line):
        match = re.match(r': \${([^\s:=]+)(:?=)([^}]+)}', line)
        return match.groups() if match else False

    def is_empty(self):
        return False if re.match('[\'"]?[^\'"]+[\'"]?', self.value) else True


class Comment:
    def __init__(self, lines, number):
        line = lines[number]
        self.value = line if line and line[0] == '#' else False
        self.line = number

    def match(self, regex):
        result = re.match(regex, self.value)
        if result:
            return result.groups()
        else:
            return False


class Shebang:
    def __init__(self, comment):
        self.line = comment.line
        result = comment.match(r'^#!(\S+)\s*(.*)')
        if result:
            self.value = result[0]
            self.args = result[1]
        else:
            self.value = False


class Rcorder:
    def __init__(self, comment):
        self.line = comment.line
        result = comment.match('# ([A-Z]+): (.+)')
        if result:
            (self.type, self.value) = (result[0], result[1].split())
        else:
            self.value = False


class Function:
    def __init__(self, lines, num):
        if len(lines[0]) > 1 and lines[0][-1] == '{':
            error.give('functions_inline_brace', num)
        elif lines[1] and lines[1][0] == '{':
            try:
                self.name = re.match(r'([\S_]+\s*)\(\)$', lines[0]).group(1)
            except:
                error.give('functions_problem', num)
            if ' ' in self.name:
                error.give('functions_problem', num)
            self.length = 0
            self.line = num
            self.value = []
            while lines[self.length] != '}':
                self.length += 1
                if self.length >= len(lines):
                    error.give('functions_neverending', num)
                    break
                self.value.append(lines[self.length])
                if self.value[-1] and self.value[-1][0] not in '\t {}':
                    error.give('functions_indent', num + self.length)
            # Remove { and } lines from length
            self.length -= 2
            logging.debug('Found function %s' % self.name)
        if not hasattr(self, 'value'):
            self.value = False

    def short(self):
        return True if self.length <= 1 else False

    def linenumbers(self):
        return list(range(self.line, self.line+self.length+3))

    def contains_line(self, line):
        return True if line in self.linenumbers() else False


def get(objlist, name):
    for o in objlist:
        if o.name == name:
            return o
    else:
        return False


def do_ports_checking(lineobj, filename):
    logging.debug('Now on ports-specific section')
    logging.debug('Checking for defaults clobbering blank values')
    for var in lineobj['Variable']:
        if var.type in ('longhand', 'shorthand'):
            if var.name.split('_')[-1] not in (
                    'enable', 'user', 'group', 'configfile', 'config'
                                              ) and var.clobber:
                error.give('variables_defaults_non_mandatory_colon', var.line)
            elif not var.clobber and var.name.split('_')[-1] in ('enable'):
                error.give('variables_defaults_mandatory_colon', var.line)
            if var.type == 'longhand' and var.name == var.source:
                error.give('variables_defaults_old_style', var.line)
    return


def do_src_checking(lineobj, filename):
    return


def do_rclint(filename):
    logging.debug('Suck in file %s' % filename)
    try:
        lines = [line.rstrip('\n') for line in open(filename)]
    except:
        logging.error('Cannot open %s for testing' % filename)
        return

    lineobj = {'Variable': [],
               'Comment': [],
               'Statement': []}

    for num in range(0, len(lines)):
        for obj in list(lineobj.keys()):
            tmp = eval(obj)(lines, num)
            if tmp.value:
                lineobj[obj].append(tmp)
                break

    lineobj['Shebang'] = [Shebang(lineobj['Comment'][0])]
    lineobj['Function'] = []

    for num in range(0, len(lines)-1):
        tmp = Function(lines[num:], num)
        if tmp.value:
            lineobj['Function'].append(tmp)

    lineobj.update({'Rcorder': []})

    for comment in lineobj['Comment']:
        tmp = Rcorder(comment)
        if tmp.value:
            lineobj['Rcorder'].append(tmp)

    logging.debug('OK, done collecting variables.  Time to check!')

    logging.debug('Checking shebang')
    if lineobj['Shebang'][0].value == False:
        error.give('shebang')

    logging.debug('Checking order of file')
    linenumbers = []
    for obj in ('Shebang', 'Rcorder'):
        for o in lineobj[obj]:
            linenumbers.append(o.line)

    for s in lineobj['Statement']:
        if s.type == 'source':
            linenumbers.append(s.line)

    for v in lineobj['Variable']:
        if v.type == 'init':
            linenumbers.append(v.line)

    for s in lineobj['Statement']:
        if s.type == 'load_rc_config':
            linenumbers.append(s.line)

    for v in lineobj['Variable']:
        if v.type != 'init':
            linenumbers.append(v.line)

    [linenumbers.append(f.line) for f in lineobj['Function']]

    for s in lineobj['Statement']:
        if s.type == 'run_rc_command':
            linenumbers.append(s.line)

    # Check lines are in the correct order

    sortedlinenumbers = sorted(linenumbers)

    for i in range(0, len(linenumbers)):
        if sortedlinenumbers[i] != linenumbers[i]:
            error.give('file_order', linenumbers[i])
            break

    logging.debug('Checking all lines are accounted for')
    for obj in list(lineobj.keys()):
        for o in lineobj[obj]:
            if hasattr(o, 'length'):
                for l in range(0, o.length):
                    linenumbers.append(o.line+l)
            else:
                linenumbers.append(o.line)
    for r in range(0, len(lines)):
        if r not in linenumbers and lines[r] != '':
            if True not in [f.contains_line(r) for f in lineobj['Function']]:
                error.give('orphaned_line', r)

    logging.debug('Checking rcorder')
    linenumbers = []
    for typ in ('PROVIDE', 'REQUIRE', 'BEFORE', 'KEYWORD'):
        for o in lineobj['Rcorder']:
            if o.type == typ:
                linenumbers.append(o.line)
    if sorted(linenumbers) != linenumbers:
        error.give('rcorder_order')

    shutdownkeyword = False
    for o in lineobj['Rcorder']:
        if o.type == 'KEYWORD':
            if 'freebsd' in [v.lower() for v in o.value]:
                error.give('rcorder_keyword_freebsd', o.line)
            elif 'shutdown' in o.value:
                shutdownkeyword = True

    if not shutdownkeyword:
        error.warn('rcorder_keyword_shutdown')

    logging.debug('Checking order of variables')
    linenumbers = []
    for typ in (('init'), ('longhand', 'shorthand'), ('basic')):
        for var in lineobj['Variable']:
            if var.type in typ:
                linenumbers.append(var.line)
    if sorted(linenumbers) != linenumbers:
        error.give('variables_order')

    logging.debug('Checking for pointless quoting and empty variables')
    for obj in lineobj['Variable']+lineobj['Statement']:
        if obj.pointless_quoted():
            error.give('value_quoted', obj.line)

    for v in lineobj['Variable']:
        if v.is_empty():
            error.give('value_empty', v.line)

    descexists = False
    for v in lineobj['Variable']:
        if v.name == 'desc':
            descexists = True
            break
    if not descexists:
        error.give('no_description')

    logging.debug('Checking for rcvar set correctly')
    for var in lineobj['Variable']:
        if var.name == 'name':
            progname = var.get_value()
        elif var.name == 'rcvar':
            try:
                if (progname + '_enable' not in var.value and
                        '${name}_enable' in var.value):
                    error.give('rcvar_incorrect', var.line)
            except:
                error.give('file_order', var.line)

    logging.debug('Checking for function issues')
    for function in lineobj['Function']:
        if function.short():
            error.give('functions_short', function.line)
        counter = 0
        for l in function.value:
            counter += 1
            if 'chown' in l:
                error.warn('functions_chown', function.line + counter)

    logging.debug('Checking for run_rc_command')
    for s in lineobj['Statement']:
        if s.type == 'run_rc_command':
            if '$1' not in s.value and '$*' not in s.value:
                error.give('run_rc_argument', s.line)

    # Strip .in from filename
    logging.debug('Checking $name agrees with PROVIDE and filename')
    fn = filename[:-3] if filename[-3:] == '.in' else filename
    fn = fn.split('/')[-1].replace('-', '_')
    try:
        n = get(lineobj['Variable'], 'name').get_value()
    except (AttributeError):
        error.critical('name_unset')
    rcordervars = []
    for r in lineobj['Rcorder']:
        if r.type != 'PROVIDE':
            continue
        for v in r.value:
            rcordervars.append(v)

    if n != fn or n not in rcordervars:
        error.give('name_mismatch')

    if mode == 'ports':
        do_ports_checking(lineobj, filename)
    if mode == 'base':
        do_src_checking(lineobj, filename)


parser = argparse.ArgumentParser()
parser.add_argument('filename', nargs='+', help="List of filenames, or '.' will scan the current port Makefile for files to check")
parser.add_argument('--language', nargs=1, type=str, default=['en'], help='sets the language that errors are reported in (available: en)')
parser.add_argument('-v', action='count', help='raises debug level; provides detailed explanations of errors')
parser.add_argument('--version', action='version', version='%s.%s.%s' %(MAJOR, MINOR, MICRO))
parser.add_argument('-b', action='store_true', help='chooses base RC script mode')
parser.add_argument('-p', action='store_true', help='chooses ports RC script mode (default)')
parser.add_argument('-k', action='store_true', help='tells rclint to carry on reporting even if there are over 10 errors')

args = parser.parse_args()
mode = 'base' if args.b else 'ports'
beaucoup_errors = args.k

verbosity = args.v if args.v is not None else 0
logging.basicConfig(level=logging.DEBUG if verbosity > 1 else logging.WARN)

error = Db('errors', args.language[0])
# problem = Db('problems', args.language[0])

filenames=[x for x in args.filename]

if filenames[0] == '.':
    rcfiles = subprocess.Popen(['make', '-VUSE_RC_SUBR'],
                                stdout=subprocess.PIPE).communicate()[0].split()
    if len(rcfiles) == 0:
        print('No RC files are in the Makefile?')
        exit()
    filenames = ['files/' + x.decode(sys.stdout.encoding) + '.in' for x in rcfiles]

for f in filenames:
    print(('Checking %s' % f))
    do_rclint(f)
