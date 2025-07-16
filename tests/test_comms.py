import pytest
from src.comms import send_command

def test_send_command(mocker):  # Use pytest-mock or similar
    mock_serial = mocker.Mock()
    mock_serial.write = mocker.Mock()
    mock_serial.readline.return_value = b'{"status": "done"}'
    assert send_command(mock_serial, 'test') == 'done'