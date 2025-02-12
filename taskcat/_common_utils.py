import collections
import logging
import os
import random
import re
import string
import sys
from collections import OrderedDict
from time import sleep

import boto3
import yaml

from taskcat.exceptions import TaskCatException

LOG = logging.getLogger(__name__)

S3_PARTITION_MAP = {
    "aws": "amazonaws.com",
    "aws-cn": "amazonaws.com.cn",
    "aws-us-gov": "amazonaws.com",
}

FIRST_CAP_RE = re.compile("(.)([A-Z][a-z]+)")
ALL_CAP_RE = re.compile("([a-z0-9])([A-Z])")


def region_from_stack_id(stack_id):
    return stack_id.split(":")[3]


def name_from_stack_id(stack_id):
    return stack_id.split(":")[5].split("/")[1]


def s3_url_maker(bucket, key, s3_client, autobucket=False):
    retries = 10
    while True:
        try:
            response = s3_client.get_bucket_location(Bucket=bucket)
            location = response["LocationConstraint"]
            break
        except s3_client.exceptions.NoSuchBucket:
            if not autobucket or retries < 1:
                raise
            retries -= 1
            sleep(5)

    # default case for us-east-1 which returns no location
    url = f"https://{bucket}.s3.us-east-1.amazonaws.com/{key}"
    if location:
        domain = get_s3_domain(location)
        url = f"https://{bucket}.s3.{location}.{domain}/{key}"
    return url


def get_s3_domain(region, ssm_client=None):
    ssm_client = ssm_client if ssm_client else boto3.client("ssm")
    partition = ssm_client.get_parameter(
        Name=f"/aws/service/global-infrastructure/regions/{region}/partition"
    )["Parameter"]["Value"]
    return S3_PARTITION_MAP[partition]


def s3_bucket_name_from_url(url):
    return url.split("//")[1].split(".")[0]


def s3_key_from_url(url):
    return "/".join(url.split("//")[1].split("/")[1:])


class CommonTools:
    def __init__(self, stack_name):
        self.stack_name = stack_name

    @staticmethod
    def regxfind(re_object, data_line):
        """
        Returns the matching string.

        :param re_object: Regex object
        :param data_line: String to be searched

        :return: Matching String if found, otherwise return 'Not-found'
        """
        security_group = re_object.search(data_line)
        if security_group:
            return str(security_group.group())
        return str("Not-found")


def exit_with_code(code, msg=""):
    if msg:
        LOG.error(msg)
    sys.exit(code)


def make_dir(path, ignore_exists=True):
    path = os.path.abspath(path)
    if ignore_exists and os.path.isdir(path):
        return
    os.makedirs(path)


def param_list_to_dict(original_keys):
    # Setup a list index dictionary.
    # - Used to give an Parameter => Index mapping for replacement.
    param_index = {}
    if not isinstance(original_keys, list):
        raise TaskCatException(
            'Invalid parameter file, outermost json element must be a list ("[]")'
        )
    for (idx, param_dict) in enumerate(original_keys):
        if not isinstance(param_dict, dict):
            raise TaskCatException(
                'Invalid parameter %s parameters must be of type dict ("{}")'
                % param_dict
            )
        if "ParameterKey" not in param_dict or "ParameterValue" not in param_dict:
            raise TaskCatException(
                f"Invalid parameter {param_dict} all items must "
                f"have both ParameterKey and ParameterValue keys"
            )
        key = param_dict["ParameterKey"]
        param_index[key] = idx
    return param_index


def merge_dicts(list_of_dicts):
    merged_dict = {}
    for single_dict in list_of_dicts:
        merged_dict = {**merged_dict, **single_dict}
    return merged_dict


def pascal_to_snake(pascal):
    sub = ALL_CAP_RE.sub(r"\1_\2", pascal)
    return ALL_CAP_RE.sub(r"\1_\2", sub).lower()


def generate_bucket_name(project: str, prefix: str = "tcat"):
    if len(prefix) > 8 or len(prefix) < 1:  # pylint: disable=len-as-condition
        raise TaskCatException("prefix must be between 1 and 8 characters long")
    alnum = string.ascii_lowercase + string.digits
    suffix = "".join(random.choice(alnum) for i in range(8))  # nosec: B311
    mid = f"-{project}-"
    avail_len = 63 - len(mid)
    mid = mid[:avail_len]
    return f"{prefix}{mid}{suffix}"


def merge_nested_dict(old, new):
    for k, v in new.items():
        if isinstance(old.get(k), dict) and isinstance(v, collections.Mapping):
            merge_nested_dict(old[k], v)
        else:
            old[k] = v


def ordered_dump(data, stream=None, dumper=yaml.Dumper, **kwds):
    class OrderedDumper(dumper):  # pylint: disable=too-many-ancestors
        pass

    def _dict_representer(dumper, data):
        return dumper.represent_mapping(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, data.items()
        )

    OrderedDumper.add_representer(OrderedDict, _dict_representer)
    return yaml.dump(data, stream, OrderedDumper, **kwds)
