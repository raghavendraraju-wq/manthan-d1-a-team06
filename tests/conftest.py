import pytest

from returnright.engine import build_engine
from tests.builders import NOW
from tests.fakes import FakeClock, FakeEmail, FakeGateway


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def engine(gateway, clock):
    return build_engine(gateway=gateway, clock=clock)


@pytest.fixture
def email():
    return FakeEmail()
