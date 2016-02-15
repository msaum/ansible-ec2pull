#!/usr/bin/env python
#
# Name: ec2pull.py
# Purpose: Provide a host inventory to Ansible 2.0 for a single VPC
# Version: 1.0
# Date: Feburary 15, 2016
# Author: Mark Saum
# Email: mark@saum.net
# Git:
#
# -------------------------------------------------------------------------------
#
#    Build a single host list from EC2 metadata to drive an Ansible pull
#
# Copyright (c) 2016, Mark Saum
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# -------------------------------------------------------------------------------
#
# Revisions:
# 1.0 - Initial build
#
# -------------------------------------------------------------------------------
#
# Environment Variables:
#
# environment variable: INSTANCEID
#     description:
#         - Limits the scope of host processing to a single
#           instance.
#         - If you want to limit the scope of a 'list' command
#           to a single host, set this value.
#     required: No
#     default: No
#
#
#  -------------------------------------------------------------------------------
# Various parts stolen from:
#  - https://github.com/ansible/ansible/blob/devel/contrib/inventory/ec2.py
# -------------------------------------------------------------------------------


__author__ = 'msaum'

import logging
import os
import re

import argparse
import boto3

import requests


try:
    import json
except ImportError:
    import simplejson as json


# -------------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------------
def main():
    """Main program entry point."""

    logger = start_logger()
    parser = configure_args()
    args = parser.parse_args()
    if args.verbose: logger.setLevel(20)
    if args.debug: logger.setLevel(10)

    logging.info('** ' + os.path.basename(__file__) + ' begin')
    # Check for INSTANCEID environment variables
    # If sets, it limits a "list" to a single instance.
    try:
        instanceid = os.environ['INSTANCEID']
        logging.info('Found environment variable INSTANCEID was set, so we will be limited to a single INSTANCEID')
        domain_name = ''
        vpcid = ''
    except Exception, e:
        try:
            instance_id = requests.get('http://169.254.169.254/latest/meta-data/instance-id')
        except Exception, e:
            logging.error('Failed to find either INSTANCEID variable or open instance metadata connection ' +
                          'to endpoint http://169.254.169.254/.', exc_info=True)
            if args.debug:
                raise
            else:
                exit(1)

    # Connect to ec2 endpoint
    try:
        ec2 = boto3.resource('ec2', region_name='us-east-1')
    except Exception, e:
        logging.error('Failed to open boto3 connection for resource ec2.', exc_info=True)
        if args.debug:
            raise
        else:
            exit(1)


    # Set AWS CLI Profile Context
    if args.profile != 'default':
        try:
            boto3.setup_default_session(profile_name=args.profile)
        except Exception, e:
            logging.error('Failed to select profile: ' + args.profile, exc_info=False)
            if args.debug:
                raise
            else:
                exit(1)
    if not args.host and not args.list:
        parser.error("You must specify either host or list mode.")
        quit()
    if args.host: host_mode(ec2, args.host, args)
    if args.list: list_mode(ec2, instanceid, args)

    logging.info('** ' + os.path.basename(__file__) + ' end')

    exit(0)


# -------------------------------------------------------------------------------
# Host Mode Switch
# host_mode
# -------------------------------------------------------------------------------
def host_mode(ec2, hostname, args):
    instance_name2id = dns_name_to_instance(ec2)
    try:
        instance_info = instance_metadata(ec2, ec2.Instance(instance_name2id[args.host]))
    except Exception, e:
        logging.error('Failed to find instance data for private_dns_name: ' + args.host, exc_info=False)
        if args.debug:
            raise
        else:
            exit(1)

    print(json.dumps(instance_info, indent=2))


# -------------------------------------------------------------------------------
# List Mode Switch
# list_mode
# -------------------------------------------------------------------------------
def list_mode(ec2, instanceid, args):
    # Define container
    inventory = empty_inventory()

    # Populate the host metadata list

    instances = ec2.instances.filter(
        Filters=[
            {'Name': 'instance-state-name', 'Values': ['running']},
            {'Name': 'instance-id', 'Values': [instanceid]}
        ]
    )


    # Populate the groups
    try:
        for instance in instances:
            instance_info = instance_metadata(ec2, instance)
            inventory['_meta']['hostvars'][instance.private_dns_name] = instance_info
            tags = tags2dict(instance.tags)
            for tag in tags.keys():
                regex = "[\:\_\-\.\/\ \(\)]"
                if inventory.has_key('tag_' + re.sub(regex, '_', tag) + '_' + re.sub(regex, '_', tags[tag])):
                    inventory['tag_' + re.sub(regex, '_', tag) + '_' + re.sub(regex, '_', tags[tag])] = \
                        inventory['tag_' + re.sub(regex, '_', tag) + '_' + re.sub(regex, '_', tags[tag])] + \
                        [instance.private_dns_name]
                else:
                    inventory['tag_' + re.sub(regex, '_', tag) + '_' + re.sub(regex, '_', tags[tag])] = \
                        [instance.private_dns_name]
    except Exception, e:
        logging.error('Failure to iterate over ec2 instances.', exc_info=False)
        exit(1)

    print(json.dumps(inventory, sort_keys=True, indent=2))


# -------------------------------------------------------------------------------
# -------------------------------------------------------------------------------
def dns_name_to_instance(ec2):
    instance_name2id = {}
    instances = ec2.instances.filter(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
    try:
        for instance in instances:
            instance_name2id[instance.private_dns_name] = instance.id
    except Exception, e:
        logging.error('Failure to iterate over ec2 instances.', exc_info=False)
        exit(1)

    return instance_name2id


# -------------------------------------------------------------------------------
# instance_metadata
#
# Apparently no solution in boto3 to do this natively...maybe rewrite for boto?
# Or maybe we live with this metada as sufficient.
# http://stackoverflow.com/questions/31630822/boto3-equivalent-to-boto-utils-get-instance-metadata
# -------------------------------------------------------------------------------
def instance_metadata(ec2, instance):
    i_metadata = dict()
    try:
        i_metadata['ec2_' + 'ami_launch_index'] = instance.ami_launch_index
        i_metadata['ec2_' + 'architecture'] = instance.architecture
        i_metadata['ec2_' + 'client_token'] = instance.client_token
        i_metadata['ec2_' + 'private_dns_name'] = instance.private_dns_name
        i_metadata['ec2_' + 'ebs_optimized'] = instance.private_dns_name
        i_metadata['ec2_' + 'hypervisor'] = instance.hypervisor
        i_metadata['ec2_' + 'image_id'] = instance.image_id
        i_metadata['ec2_' + 'instance_id'] = instance.instance_id
        if instance.instance_lifecycle == None:
            i_metadata['ec2_' + 'instance_lifecycle'] = ''
        else:
            i_metadata['ec2_' + 'instance_lifecycle'] = instance.instance_lifecycle
        i_metadata['ec2_' + 'instance_type'] = instance.instance_type
        if instance.kernel_id == None:
            i_metadata['ec2_' + 'kernel_id'] = ''
        else:
            i_metadata['ec2_' + 'kernel_id'] = instance.kernel_id
        i_metadata['ec2_' + 'key_name'] = instance.key_name
        if instance.platform == None:
            i_metadata['ec2_' + 'platform'] = ''
        else:
            i_metadata['ec2_' + 'platform'] = instance.platform
        i_metadata['ec2_' + 'platform'] = instance.platform
        i_metadata['ec2_' + 'private_dns_name'] = instance.private_dns_name
        i_metadata['ec2_' + 'private_ip_address'] = instance.private_ip_address
        i_metadata['ec2_' + 'public_dns_name'] = instance.public_dns_name
        i_metadata['ec2_' + 'public_ip_address'] = instance.public_ip_address
        if instance.ramdisk_id == None:
            i_metadata['ec2_' + 'ramdisk_id'] = ''
        else:
            i_metadata['ec2_' + 'ramdisk_id'] = instance.ramdisk_id
        i_metadata['ec2_' + 'root_device_name'] = instance.root_device_name
        i_metadata['ec2_' + 'root_device_type'] = instance.root_device_type
        i_metadata['ec2_' + 'source_dest_check'] = instance.source_dest_check
        if instance.spot_instance_request_id == None:
            i_metadata['ec2_' + 'spot_instance_request_id'] = ''
        else:
            i_metadata['ec2_' + 'spot_instance_request_id'] = instance.spot_instance_request_id
        if instance.sriov_net_support == None:
            i_metadata['ec2_' + 'sriov_net_support'] = ''
        else:
            i_metadata['ec2_' + 'sriov_net_support'] = instance.sriov_net_support
        i_metadata['ec2_' + 'state_transition_reason'] = instance.state_transition_reason
        i_metadata['ec2_' + 'subnet_id'] = instance.subnet_id
        i_metadata['ec2_' + 'virtualization_type'] = instance.virtualization_type
        i_metadata['ec2_' + 'vpc_id'] = instance.vpc_id
    except Exception, e:
        logging.info(e)
        pass

    return i_metadata


# -------------------------------------------------------------------------------
# empty_inventory
# -------------------------------------------------------------------------------
def empty_inventory():
    return {"_meta": {"hostvars": {}}}


# -------------------------------------------------------------------------------
# configure_args
# -------------------------------------------------------------------------------
def configure_args():
    parser = argparse.ArgumentParser(description='Provides a list of all VPCs in an account')
    parser.add_argument("--debug", "-d", help="turn on debugging output", action="store_true")
    parser.add_argument("--verbose", "-v", help="turn on verbose output", action="store_true")
    parser.add_argument("--list", help="List groups", action="store_true")
    parser.add_argument("--host", help="List hosts", type=str)
    parser.add_argument('--profile', type=str, default='default')
    return parser


# -------------------------------------------------------------------------------
# logger
# -------------------------------------------------------------------------------
def start_logger():
    return logging.getLogger()


# -------------------------------------------------------------------------------
# tags2dict
# -------------------------------------------------------------------------------
def tags2dict(tags):
    '''Converts a Filter tag list to a dict
    :param dict tags: A dict of Filter tag list
    :return: A simple key / value dict
    :rtype: dict
    Stolen from: https://github.com/iMilnb/awstools/blob/80e3ea0778221ceb6846b6d401f3d03d6a01e96f/mods/session.py
    '''
    ret = {}
    for t in tags:
        ret[t['Key']] = t['Value']
    return ret


# -------------------------------------------------------------------------------
# Main Entry Point
# -------------------------------------------------------------------------------
if __name__ == "__main__":
    # execute only if run as a script
    main()
