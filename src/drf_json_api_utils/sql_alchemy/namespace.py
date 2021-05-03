#
# ###################################################################################################################
#  ______   _______        __    _  _______  _______        ______   _______  ___      _______  _______  _______
# |      | |       |      |  |  | ||       ||       |      |      | |       ||   |    |       ||       ||       |
# |  _    ||   _   |      |   |_| ||   _   ||_     _|      |  _    ||    ___||   |    |    ___||_     _||    ___|
# | | |   ||  | |  |      |       ||  | |  |  |   |        | | |   ||   |___ |   |    |   |___   |   |  |   |___
# | |_|   ||  |_|  |      |  _    ||  |_|  |  |   |        | |_|   ||    ___||   |___ |    ___|  |   |  |    ___|
# |       ||       |      | | |   ||       |  |   |        |       ||   |___ |       ||   |___   |   |  |   |___
# |______| |_______|      |_|  |__||_______|  |___|        |______| |_______||_______||_______|  |___|  |_______|
#
# ###################################################################################################################
#
# This library uses reflection to generate all the boilerplate types that DRF needs in order to create views. This is
# the path where all the reflected types are saved at, meaning when referencing them they will show as a full path
# of `drf_json_api_utils.sql_alchemy.namespace.[REFLECTED_TYPE_NAME_HERE]`.
#
# Read about reflection here: https://en.wikibooks.org/wiki/Python_Programming/Reflection
#

_TYPE_TO_SCHEMA = {}
