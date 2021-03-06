#!/usr/bin/env python

# Author: Zhang Huangbin <zhb@iredmail.org>
# Purpose: Cleanup expired throttle and greylisting tracking records.

import os
import sys
import time
import web

os.environ['LC_ALL'] = 'C'

rootdir = os.path.abspath(os.path.dirname(__file__)) + '/../'
sys.path.insert(0, rootdir)

import settings
from tools import logger, get_db_conn, sql_count_id

web.config.debug = False

backend = settings.backend
logger.info('* Backend: %s' % backend)

now = int(time.time())

conn_iredapd = get_db_conn('iredapd')

#
# Throttling
#
logger.info('* Remove expired throttle tracking records.')

# count existing records, delete, count left records
total_before = sql_count_id(conn_iredapd, 'throttle_tracking')
conn_iredapd.delete('throttle_tracking', where='init_time + period < %d' % now)
total_after = sql_count_id(conn_iredapd, 'throttle_tracking')

logger.info('\t- %d removed, %d left.' % (total_before - total_after, total_after))

#
# Greylisting tracking records.
#
logger.info('* Remove expired greylisting tracking records.')

# count existing records, delete, count left records
total_before = sql_count_id(conn_iredapd, 'greylisting_tracking')
conn_iredapd.delete('greylisting_tracking', where='record_expired < %d' % now)
total_after = sql_count_id(conn_iredapd, 'greylisting_tracking')

#
# Some basic analyzation
#
# Count how many records are passed greylisting
total_passed = 0
qr = conn_iredapd.select('greylisting_tracking',
                         what='count(id) as total',
                         where='passed=1')
if qr:
    total_passed = qr[0].total

logger.info('\t- %d removed, %d left (%d passed, %d not).' % (
    total_before - total_after,
    total_after,
    total_passed,
    total_after - total_passed))
