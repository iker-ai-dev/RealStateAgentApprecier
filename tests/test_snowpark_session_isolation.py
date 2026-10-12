import sys
import types
import unittest
from unittest.mock import Mock, patch

from app.data_access import get_managed_snowpark_session, get_snowpark_session


class SessionIsolationTests(unittest.TestCase):
    def test_web_backend_never_borrows_another_requests_active_session(self):
        owned = Mock()
        builder = Mock()
        builder.configs.return_value.create.return_value = owned
        with patch.dict(sys.modules, {'_snowflake': None}), \
                patch('snowflake.snowpark.Session.get_active_session') as active, \
                patch('snowflake.snowpark.Session.builder', builder):
            self.assertIsNone(get_managed_snowpark_session())
            self.assertIs(get_snowpark_session({'account': 'test'}), owned)
            active.assert_not_called()
            builder.configs.assert_called_once()

    def test_snowflake_host_borrows_managed_session(self):
        managed = Mock()
        with patch.dict(sys.modules, {'_snowflake': types.ModuleType('_snowflake')}), \
                patch('snowflake.snowpark.Session.get_active_session', return_value=managed):
            self.assertIs(get_snowpark_session({'account': 'test'}), managed)
            managed.close.assert_not_called()
