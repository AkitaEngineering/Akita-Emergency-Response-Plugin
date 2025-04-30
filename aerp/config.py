# aerp/config.py
# Copyright (C) 2025 Akita Engineering / www.akitaengineering.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""
Configuration management for the Akita Emergency Response Plugin (AERP).
Handles loading, validation, and accessing configuration settings from a JSON file.
"""

import json
import logging
import os
from .constants import (
    DEFAULT_INTERVAL, DEFAULT_EMERGENCY_PORT, DEFAULT_EMERGENCY_MESSAGE,
    DEFAULT_ALERT_RADIUS, DEFAULT_ACK_TIMEOUT, DEFAULT_ENABLED_BY_DEFAULT,
    CONFIG_INTERVAL, CONFIG_PORT, CONFIG_MESSAGE, CONFIG_RADIUS,
    CONFIG_ACK_TIMEOUT, CONFIG_ENABLED
)

# Get a logger specific to this module
logger = logging.getLogger(__name__)

class ConfigManager:
    """
    Manages loading, validation, and access of AERP configuration from a JSON file.

    Attributes:
        config_file (str): Path to the configuration file.
        config (dict): The loaded and validated configuration dictionary.
    """

    def __init__(self, config_file="config/aerp_config.json"):
        """
        Initializes the ConfigManager.

        Args:
            config_file (str): The path to the JSON configuration file.
                               Defaults to "config/aerp_config.json".
        """
        self.config_file = config_file
        self.config = self._get_default_config() # Start with defaults
        self.load_config() # Load and validate from file

    def _get_default_config(self):
        """
        Returns a dictionary containing the default configuration values.

        Returns:
            dict: The default configuration settings.
        """
        return {
            CONFIG_INTERVAL: DEFAULT_INTERVAL,
            CONFIG_PORT: DEFAULT_EMERGENCY_PORT,
            CONFIG_MESSAGE: DEFAULT_EMERGENCY_MESSAGE,
            CONFIG_RADIUS: DEFAULT_ALERT_RADIUS,
            CONFIG_ACK_TIMEOUT: DEFAULT_ACK_TIMEOUT,
            CONFIG_ENABLED: DEFAULT_ENABLED_BY_DEFAULT,
        }

    def _validate_config(self, loaded_config):
        """
        Validates the structure and data types of a loaded configuration dictionary.
        If values are invalid or missing, defaults are used, and warnings are logged.

        Args:
            loaded_config (dict): The configuration dictionary loaded from the file.

        Returns:
            dict: A validated configuration dictionary, potentially filled with defaults.
        """
        if not isinstance(loaded_config, dict):
             logger.error("Loaded configuration is not a dictionary. Using all defaults.")
             return self._get_default_config()

        validated_config = self._get_default_config() # Start with defaults
        validation_errors = []

        # Iterate through the *default* keys to ensure all expected keys are checked
        for key, default_value in validated_config.items():
            if key in loaded_config:
                value = loaded_config[key]
                expected_type = type(default_value)

                # Check type consistency
                if isinstance(value, expected_type):
                    # Perform value-specific validation
                    valid = True
                    error_msg = ""
                    if key == CONFIG_INTERVAL and value <= 0:
                        valid = False
                        error_msg = f"'{key}' must be a positive integer."
                    elif key == CONFIG_PORT and not (0 <= value <= 511):
                        valid = False
                        error_msg = f"'{key}' must be an integer between 0 and 511 (inclusive)."
                    elif key == CONFIG_RADIUS and value < 0:
                         valid = False
                         error_msg = f"'{key}' (alert radius) cannot be negative (use 0 to disable)."
                    elif key == CONFIG_ACK_TIMEOUT and value <= 0:
                        valid = False
                        error_msg = f"'{key}' must be a positive integer."
                    elif key == CONFIG_MESSAGE and not value: # Check for empty string
                         valid = False
                         error_msg = f"'{key}' cannot be an empty string."

                    if valid:
                        validated_config[key] = value # Assign the valid value from the file
                    else:
                        validation_errors.append(f"Invalid value for '{key}': {value}. {error_msg} Using default: {default_value}.")
                        # Default value remains in validated_config

                else: # Type mismatch
                    validation_errors.append(f"Incorrect type for '{key}'. Expected {expected_type.__name__}, got {type(value).__name__}. Using default: {default_value}.")
                    # Default value remains in validated_config
            else:
                # Key missing from loaded_config, default is already set in validated_config
                validation_errors.append(f"Config key '{key}' missing. Using default: {default_value}.")

        # Log all validation errors together
        if validation_errors:
            logger.warning("Configuration validation issues found:")
            for error in validation_errors:
                logger.warning(f" - {error}")

        return validated_config

    def load_config(self):
        """
        Loads configuration from the JSON file specified during initialization.
        - If the file doesn't exist, it creates one with default values.
        - If the file is invalid (bad JSON, wrong types), it logs errors and uses defaults.
        - Updates the `self.config` attribute.
        """
        config_dir = os.path.dirname(self.config_file)

        # Ensure the configuration directory exists
        if config_dir and not os.path.exists(config_dir):
            try:
                os.makedirs(config_dir, exist_ok=True)
                logger.info(f"Created configuration directory: '{config_dir}'")
            except OSError as e:
                logger.error(f"Failed to create configuration directory '{config_dir}': {e}. Proceeding without saving defaults.")
                # Cannot create default file, will just use in-memory defaults
                self.config = self._get_default_config()
                return

        # Check if the config file exists
        if not os.path.exists(self.config_file):
            logger.warning(f"Configuration file '{self.config_file}' not found.")
            try:
                logger.info(f"Creating default configuration file at '{self.config_file}'.")
                with open(self.config_file, "w") as f:
                    json.dump(self._get_default_config(), f, indent=4)
                self.config = self._get_default_config() # Use defaults after creating the file
            except IOError as e:
                logger.error(f"Failed to create default configuration file '{self.config_file}': {e}. Using default values.")
                self.config = self._get_default_config()
            return

        # File exists, try to load and validate it
        try:
            with open(self.config_file, "r") as f:
                loaded_config = json.load(f)
                self.config = self._validate_config(loaded_config)
                logger.info(f"Successfully loaded and validated configuration from '{self.config_file}'")
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from '{self.config_file}': {e}. Using default values.")
            self.config = self._get_default_config()
        except IOError as e:
            logger.error(f"Error reading configuration file '{self.config_file}': {e}. Using default values.")
            self.config = self._get_default_config()
        except Exception as e:
             # Catch any other unexpected errors during loading/validation
             logger.exception(f"Unexpected error processing configuration file '{self.config_file}': {e}. Using default values.")
             self.config = self._get_default_config()

    def get(self, key, default=None):
        """
        Safely retrieves a configuration value by its key.

        Args:
            key (str): The configuration key to retrieve.
            default: The value to return if the key is not found. Defaults to None.

        Returns:
            The configuration value associated with the key, or the default value.
        """
        return self.config.get(key, default)

    def __getitem__(self, key):
        """
        Allows dictionary-style access to configuration values (e.g., config_manager['interval']).
        Raises KeyError if the key is not found.

        Args:
            key (str): The configuration key.

        Returns:
            The configuration value.

        Raises:
            KeyError: If the key does not exist in the configuration.
        """
        # This assumes validation has already happened and self.config contains expected keys
        return self.config[key]

    def __str__(self):
        """
        Returns a string representation of the current configuration.
        """
        return json.dumps(self.config, indent=4)

