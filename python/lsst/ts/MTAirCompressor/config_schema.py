__all__ = ["CONFIG_SCHEMA"]

import yaml


CONFIG_SCHEMA = yaml.safe_load(
    """
$schema: http://json-schema.org/draft-07/schema#
$id: https://github.com/lsst-ts/ts_MTAirCompressor/blob/master/schema/aircompressor.yaml
# title must end with one or more spaces followed by the schema version, which must begin with "v"
title: AirCompressor v1
description: Schema for MT Air Compressor CSC configuration files
type: object
properties:
  grace_period:
    description: >-
      number of seconds for which connection can be lost without failing
    type: number
requiredProperties: false
additionalProperties: false
"""
)
