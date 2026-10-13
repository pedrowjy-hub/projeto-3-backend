import pytest
from unittest.mock import MagicMock, patch
from api import app

@pytest.fixture
def client():
    app.config['TESTING'] = True 
    with app.test_client() as test_client:
        yield test_client