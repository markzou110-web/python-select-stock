import sys
import os
import json
import pytest
from unittest.mock import Mock, patch, mock_open, MagicMock

# Add parent directory of 'backend' to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import load_db_config, save_db_config, get_db_engine, init_db, save_setting, get_setting

class TestDbConfig:
    def test_load_db_config_success(self):
        """Test loading configuration from a file successfully."""
        config_data = {"user": "test", "pwd": "password"}
        with patch("builtins.open", mock_open(read_data=json.dumps(config_data))):
            with patch("os.path.exists", return_value=True):
                 assert load_db_config() == config_data

    def test_load_db_config_no_file(self):
        """Test behavior when config file does not exist."""
        with patch("os.path.exists", return_value=False):
            assert load_db_config() == {}

    def test_load_db_config_error(self):
        """Test handling of empty or malformed config file."""
        with patch("builtins.open", side_effect=Exception("Read error")):
            with patch("os.path.exists", return_value=True):
                assert load_db_config() == {}

    def test_save_db_config(self):
        """Test saving configuration to a file."""
        config_data = {"user": "test"}
        with patch("builtins.open", mock_open()) as mock_file:
            save_db_config(config_data)
            mock_file.assert_called_once()
            # Verify write occurred
            handle = mock_file()
            handle.write.assert_called()

class TestDbEngine:
    @patch("core.db.create_engine")
    @patch("core.db.load_db_config")
    def test_get_db_engine_success(self, mock_load_config, mock_create):
        """Test successful engine creation."""
        mock_load_config.return_value = {
            "user": "u", "pwd": "p", "host": "h", "port": "5432", "db": "d"
        }
        engine_mock = Mock()
        mock_create.return_value = engine_mock
        
        engine = get_db_engine()
        assert engine is engine_mock
        mock_create.assert_called_once()

    @patch("core.db.load_db_config")
    def test_get_db_engine_fail_config(self, mock_load_config):
        """Test engine creation failure due to missing config."""
        mock_load_config.return_value = {}
        assert get_db_engine() is None

class TestInitDb:
    def test_init_db(self):
        """Test database initialization SQL execution."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        # Mock context manager behavior
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        
        init_db(mock_engine)
        
        # Verify multiple execute calls were made for table creation
        assert mock_conn.execute.call_count > 0

class TestSettings:
    def test_get_setting_success(self):
        """Test retrieving a setting value."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        
        # Mock result proxy
        mock_result = Mock()
        mock_result.fetchone.return_value = ["some_value"]
        mock_conn.execute.return_value = mock_result
        
        val = get_setting("some_key", engine=mock_engine)
        assert val == "some_value"

    def test_save_setting_success(self):
        """Test saving a setting value."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        
        res = save_setting("key", "value", engine=mock_engine)
        assert res is True
        mock_conn.commit.assert_called_once()
