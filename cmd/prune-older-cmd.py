#!/bin/sh
"""": # -*-python-*-
bup_python="$(dirname "$0")/bup-python" || exit $?
exec "$bup_python" "$0" ${1+"$@"}
"""
# end of bup preamble

from __future__ import print_function
from collections import defaultdict
from itertools import groupby
from sys import stderr
from time import localtime, strftime, time
import re, sys

from bup import git, options
from bup.gc import bup_gc
from bup.helpers import die_if_errors, partition, period_as_secs
from bup.rm import bup_rm


def branches(refnames=()):
    return ((name[11:], sha) for (name,sha)
            in git.list_refs(refnames=('refs/heads/' + n for n in refnames),
                             limit_to_heads=True))

def save_name(branch, utc):
    return branch + '/' + strftime('%Y-%m-%d-%H%M%S', localtime(utc))

def classify_saves(saves, period_start):
    """Yield ('remove', (utc, id)) or ('retain', (utc, id)) for
    each (utc, id) in saves.  The ids are binary hashes."""

    def retain_oldest_in_region(region):
        prev = None
        for save in region:
            if prev:
                yield 'remove', prev
            prev = save
        if prev:
            yield 'retain', prev

    matches, rest = partition(lambda s: s[0] >= period_start['all'], saves)
    for save in matches:
        yield 'retain', save

    tm_ranges = ((period_start['dailies'], lambda s: localtime(s[0]).tm_yday),
                 (period_start['monthlies'], lambda s: localtime(s[0]).tm_mon),
                 (period_start['yearlies'], lambda s: localtime(s[0]).tm_year))

    for pstart, time_region_id in tm_ranges:
        matches, rest = partition(lambda s: s[0] >= pstart, rest)
        for region_id, region_saves in groupby(matches, time_region_id):
            for action in retain_oldest_in_region(region_saves):
                yield action

    for save in rest:
        yield 'remove', save


optspec = """
bup prune-older [options...] [BRANCH...]
--
keep-all-for=       retain all saves within the PERIOD
keep-dailies-for=   retain the oldest save per day within the PERIOD
keep-monthlies-for= retain the oldest save per month within the PERIOD
keep-yearlies-for=  retain the oldest save per year within the PERIOD
wrt=                end all periods at this number of seconds since the epoch
pretend       don't prune, just report intended actions to standard output
gc            collect garbage after removals [1]
gc-threshold= only rewrite a packfile if it's over this percent garbage [10]
#,compress=   set compression level to # (0-9, 9 is highest) [1]
v,verbose     increase log output (can be used more than once)
unsafe        use the command even though it may be DANGEROUS
"""

o = options.Options(optspec)
opt, flags, roots = o.parse(sys.argv[1:])

if not opt.unsafe:
    o.fatal('refusing to run dangerous, experimental command without --unsafe')

now = int(time()) if not opt.wrt else opt.wrt
if not isinstance(now, (int, long)):
    o.fatal('--wrt value ' + str(now) + ' is not an integer')

period_start = {}
for period, extent in (('all', opt.keep_all_for),
                       ('dailies', opt.keep_dailies_for),
                       ('monthlies', opt.keep_monthlies_for),
                       ('yearlies', opt.keep_yearlies_for)):
    if extent:
        secs = period_as_secs(extent)
        if not secs:
            o.fatal('%r is not a valid period' % extent)
        period_start[period] = now - secs

if not period_start:
    o.fatal('at least one keep argument is required')

period_start = defaultdict(lambda: float('inf'), period_start)

if opt.verbose:
    for kind in ['all', 'dailies', 'monthlies', 'yearlies']:
        period_utc = period_start[kind]
        if period_utc != float('inf'):
            when = strftime('%Y-%m-%d-%H%M%S', localtime(period_utc)) \
                if period_utc > float('-inf') else 'forever'
            print('keeping', kind, 'since', when, file=stderr)

git.check_repo_or_die()

# This could be more efficient, but for now just build the whole list
# in memory and let bup_rm() do some redundant work.

removals = []
for branch, branch_id in branches(roots):
    die_if_errors()
    saves = git.rev_list(branch_id.encode('hex'))
    for action, (utc, id) in classify_saves(saves, period_start):
        assert(action in ('remove', 'retain'))
        # FIXME: base removals on hashes
        if opt.pretend:
            print(action, save_name(branch, utc))
        elif action == 'remove':
            removals.append(save_name(branch, utc))

if not opt.pretend:
    die_if_errors()
    bup_rm(removals, compression=opt.compress, verbosity=opt.verbose)
    if opt.gc:
        die_if_errors()
        bup_gc(threshold=opt.gc_threshold,
               compression=opt.compress,
               verbosity=opt.verbose)

die_if_errors()
