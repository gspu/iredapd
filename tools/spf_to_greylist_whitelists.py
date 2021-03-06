#!/usr/bin/env python

# Author: Zhang Huangbin <zhb@iredmail.org>
# Purpose: Query SPF DNS record of specified domains and import returned IP
#          addresses/networks in to iRedAPD database as greylisting whitelists.

#
# USAGE
#
#   You can run this script with or without arguments.
#
#   1) Run this script without any arguments:
#
#       $ python spf_to_greylist_whitelists.py
#
#      It will query SQL table `iredapd.greylisting_whitelist_domains` to get
#      the mail domain names.
#
#   2) Run this script with mail domain names which you want to disable
#      gryelisting:
#
#       $ python spf_to_greylist_whitelists.py <domain> [domain ...]
#
#      For example:
#
#       $ python spf_to_greylist_whitelists.py google.com aol.com
#
#   3) Run this script with option '--submit' and domain name to add domain
#      name to SQL table `iredapd.greylisting_whitelist_domains`, and query
#      its SPF/MX/A records immediately, and remove all greylisting tracking
#      data (since it's whitelisted, we don't need the tracking data anymore):
#
#       $ python spf_to_greylist_whitelists.py --submit <domain> [domain ...]
#
# Required third-party Python modules:
#
#   - dnspython: https://pypi.python.org/pypi/dnspython
#   - web.py: http://webpy.org/

#
# KNOWN ISSUES
#
#   * not supported spf syntax:
#
#       - -all
#       - a/24 a:<domain>/24
#       - mx/24 mx:<domain>/24
#       - exists:<domain>

#
# REFERENCES
#
#   * SPF Record Syntax: http://www.openspf.org/SPF_Record_Syntax
#   * A simpler shell script which does the same job, it lists all IP addresses
#     and/or networks on terminal: https://bitbucket.org/zhb/spf-to-ip

import os
import sys
import logging
import web

try:
    from dns import resolver
except ImportError:
    print "<<< ERROR >>> Please install Python module 'dnspython' first."

os.environ['LC_ALL'] = 'C'

rootdir = os.path.abspath(os.path.dirname(__file__)) + '/../'
sys.path.insert(0, rootdir)

from tools import logger, get_db_conn
from libs import utils

if '--debug' in sys.argv:
    logger.setLevel(logging.DEBUG)
    sys.argv.remove('--debug')
else:
    logger.setLevel(logging.INFO)

# Add domain name to SQL table `iredapd.greylisting_whitelist_domains`
submit_to_sql_db = False
if '--submit' in sys.argv:
    submit_to_sql_db = True
    sys.argv.remove('--submit')


def query_a(domains, queried_domains=None, returned_ips=None):
    "Return list of IP addresses/networks defined in A record of domain name."
    ips = set()

    queried_domains = queried_domains or set()
    returned_ips = returned_ips or set()

    domains = [d for d in domains if d not in queried_domains]
    for domain in domains:
        try:
            qr = resolver.query(domain, 'A')
            if qr:
                for r in qr:
                    _ip = str(r)
                    logger.debug('\t\t+ [%s] A: %s' % (domain, _ip))
                    ips.add(_ip)

                    returned_ips.add(_ip)

            queried_domains.add('a:' + domain)
        except:
            pass

    return {'ips': ips,
            'queried_domains': queried_domains,
            'returned_ips': returned_ips}


def query_mx(domains, queried_domains=None, returned_ips=None):
    "Return list of IP addresses/networks defined in MX record of domain name."
    ips = set()

    queried_domains = queried_domains or set()
    returned_ips = returned_ips or set()

    a = set()

    domains = [d for d in domains if d not in queried_domains]
    for domain in domains:
        try:
            qr = resolver.query(domain, 'MX')
            if qr:
                for r in qr:
                    hostname = str(r).split()[-1].rstrip('.')
                    logger.debug('\t\t+ [%s] MX: %s' % (domain, hostname))
                    a.add(hostname)

            if a:
                qr = query_a(a, queried_domains=queried_domains, returned_ips=returned_ips)

                ips_a = qr['ips']
                queried_domains = qr['queried_domains']
                returned_ips = qr['returned_ips']

                ips.update(ips_a)

            queried_domains.add('mx:' + domain)
        except:
            pass

    return {'ips': ips,
            'queried_domains': queried_domains,
            'returned_ips': returned_ips}


def query_spf(domain, queried_domains=None):
    """Return spf record of given domain."""
    spf = None

    queried_domains = queried_domains or set()
    if 'spf:' + domain in queried_domains:
        return {'spf': None,
                'queried_domains': queried_domains}

    # WARNING: DO NOT UPDATE queried_domains in this function
    try:
        qr = resolver.query(domain, 'TXT')
        for r in qr:
            # Remove heading/ending quotes
            r = str(r).strip('"').strip("'")

            # Some SPF records contains splited IP address like below:
            #   v=spf1 ... ip4:66.220.157" ".0/25 ...
            # We should remove '"' and combine them.
            _v = [v for v in r.split('"') if not v.startswith(' ')]
            r = ''.join(_v)

            if r.startswith('v=spf1'):
                spf = r
                break
    except:
        pass

    queried_domains.add('spf:' + domain)

    return {'spf': spf,
            'queried_domains': queried_domains}


def parse_spf(domain, spf, queried_domains=None, returned_ips=None):
    """Parse spf record."""
    ips = set()
    a = set()
    mx = set()
    included_domains = set()

    queried_domains = queried_domains or set()
    returned_ips = returned_ips or set()

    if not spf:
        return {'ips': ips,
                'queried_domains': queried_domains,
                'returned_ips': returned_ips}

    tags = spf.split()

    for tag in tags:
        v = tag.split(':', 1)[-1]

        if tag.startswith('include:'):
            included_domains.add(v)
        elif tag.startswith('redirect='):
            d = tag.split('=', 1)[-1]
            included_domains.add(d)
        elif tag.startswith('ip4:') \
                or tag.startswith('+ip4:') \
                or tag.startswith('ip6:') \
                or tag.startswith('+ip6:'):
            ips.add(v)
        elif tag.startswith('a:') or tag.startswith('+a:'):
            a.add(v)
        elif tag.startswith('mx:') or tag.startswith('+mx:'):
            mx.add(v)
        elif tag.startswith('ptr:'):
            ips.add('@' + v)
        elif tag == 'a' or tag == '+a':
            a.add(domain)
        elif tag == 'mx' or tag == '+mx':
            mx.add(domain)
        elif tag == 'ptr':
            ips.add('@' + domain)

    # Find IP in included_domains
    if included_domains:
        included_domains = [i for i in included_domains if 'spf:' + i not in queried_domains]

        logger.debug('\t\t+ [%s] include: -> %s' % (domain, ', '.join(included_domains)))
        qr = query_spf_of_included_domains(included_domains,
                                           queried_domains=queried_domains,
                                           returned_ips=returned_ips)

        ips_included = qr['ips']
        queried_domains = qr['queried_domains']
        returned_ips = qr['returned_ips']

        ips.update(ips_included)

    if a:
        a = [i for i in a if 'a:' + i not in queried_domains]

        logger.debug('\t\t+ [%s] A -> %s' % (domain, ', '.join(a)))
        qr = query_a(a, queried_domains=queried_domains, returned_ips=returned_ips)

        ips_a = qr['ips']
        queried_domains = qr['queried_domains']
        returned_ips = qr['returned_ips']

        ips.update(ips_a)

    if mx:
        mx = [i for i in mx if 'mx:' + i not in queried_domains]

        logger.debug('\t\t+ [%s] MX -> %s' % (domain, ', '.join(mx)))
        qr = query_mx(mx, queried_domains=queried_domains, returned_ips=returned_ips)

        ips_mx = qr['ips']
        queried_domains = qr['queried_domains']
        returned_ips = qr['returned_ips']

        ips.update(ips_mx)

    queried_domains.add('spf:' + domain)

    return {'ips': ips,
            'queried_domains': queried_domains,
            'returned_ips': returned_ips}


def query_spf_of_included_domains(domains, queried_domains=None, returned_ips=None):
    """Return set of IP addresses and/or networks defined in SPF record of
    given mail domain names."""
    ips = set()

    queried_domains = queried_domains or set()
    returned_ips = returned_ips or set()

    domains = [d for d in domains if 'spf:' + d not in queried_domains]
    for domain in domains:
        qr = query_spf(domain, queried_domains=queried_domains)
        spf = qr['spf']
        queried_domains = qr['queried_domains']

        if spf:
            logger.debug('\t\t+ [include: %s] %s' % (domain, spf))
        else:
            logger.debug('\t\t+ [include: %s] empty' % domain)

        qr = parse_spf(domain, spf, queried_domains=queried_domains, returned_ips=returned_ips)

        ips_spf = qr['ips']
        queried_domains = qr['queried_domains']
        returned_ips = qr['returned_ips']

        ips.update(ips_spf)

        queried_domains.add('spf:' + domain)
        returned_ips.update(ips_spf)

    return {'ips': ips,
            'queried_domains': queried_domains,
            'returned_ips': returned_ips}


web.config.debug = False
conn = get_db_conn('iredapd')

if len(sys.argv) == 1:
    logger.info('* Query SQL server to get mail domain names.')

    domains = []

    qr = conn.select('greylisting_whitelist_domains', what='domain')
    for r in qr:
        domains.append(r.domain)
else:
    domains = sys.argv[1:]

domains = [d for d in domains if utils.is_domain(d)]
if not domains:
    logger.info('* No valid domain names, exit.')
    sys.exit()

logger.info('* Parsing domains, %d in total.' % len(domains))

all_ips = set()
domain_ips = {}
queried_domains = set()
returned_ips = set()

for domain in domains:
    # Convert domain name to lower cases.
    domain = domain.lower()

    if 'spf:' + domain in queried_domains:
        continue

    logger.info('\t+ [%s]' % domain)

    # Query SPF record
    qr = query_spf(domain, queried_domains=queried_domains)
    spf = qr['spf']
    queried_domains = qr['queried_domains']

    if spf:
        logger.debug('\t\t+ SPF -> %s' % spf)

        # Parse returned SPF record
        qr = parse_spf(domain, spf, queried_domains=queried_domains, returned_ips=returned_ips)
    else:
        # Whitelist hosts listed in MX records.
        qr = query_mx([domain], queried_domains=queried_domains, returned_ips=returned_ips)

    ips = qr['ips']
    queried_domains = qr['queried_domains']
    returned_ips = qr['returned_ips']

    domain_ips[domain] = ips
    all_ips.update(ips)

    logger.debug('\t\t+ Result: %s' % ips)

if not all_ips:
    logger.info('* No IP address/network found. Exit.')
    sys.exit()

# Import IP addresses/networks as greylisting whitelists.
for domain in domain_ips:
    comment = 'AUTO-UPDATE: %s' % domain
    sql_vars = {'domain': domain,
                'account': '@.',
                'comment': comment}

    # Delete old records
    try:
        conn.delete('greylisting_whitelist_domain_spf',
                    vars=sql_vars,
                    where="comment=$comment")

        # in iRedAPD-2.0 and earlier releases, results were stored in
        # sql table `greylisting_whitelists`
        conn.delete('greylisting_whitelists',
                    vars=sql_vars,
                    where="comment=$comment")
    except Exception, e:
        logger.info('* <<< ERROR >>> Cannot delete old record for domain %s: %s' % (domain, str(e)))

    # Insert new records
    for ip in domain_ips[domain]:
        # Remove host bit in IPv4 address: x.x.x.Y/zz -> x.x.x.Y
        if ':' not in ip:
            # IPv4
            _last_ip_field = ip.split('.')[-1]

            if ('/' in _last_ip_field) and (not _last_ip_field.startswith('0/')):
                # IPv4 network or IPv4 with host bit
                ip = ip.split('/', 1)[0]

        try:
            # Check whether we already have this sender. used to avoid annoying
            # warning message in PostgreSQL log file due to duplicate key.
            qr = conn.select('greylisting_whitelist_domain_spf',
                             vars={'account': '@.', 'sender': ip},
                             what='id',
                             where='account=$account AND sender=$sender',
                             limit=1)

            if not qr:
                # Insert new whitelist
                conn.insert('greylisting_whitelist_domain_spf',
                            account='@.',
                            sender=ip,
                            comment=comment)
        except Exception, e:
            if e.__class__.__name__ == 'IntegrityError':
                pass
            else:
                logger.error('* <<< ERROR >>> Cannot insert new record for domain %s: %s' % (domain, e.message))

if submit_to_sql_db:
    logger.info('* Store domain names in SQL database as greylisting whitelists.')
    for d in domains:
        try:
            conn.insert('greylisting_whitelist_domains', domain=d)
        except Exception, e:
            logger.error('<<< ERROR >>> %s' % str(e))
