"""FR-703c: every test runs without provider keys or IP network connections."""
import socket
import pytest

@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    for key in ('CEREBRO_API_KEY','OPENAI_API_KEY','CEREBRO_MODEL','CEREBRO_BASE_URL','CEREBRO_MODEL_REVISION'):
        monkeypatch.delenv(key,raising=False)
    connect,connect_ex=socket.socket.connect,socket.socket.connect_ex
    def block_ip(self,address):
        if self.family in (socket.AF_INET,socket.AF_INET6): raise AssertionError('IPv4/IPv6 disabled in offline tests')
        return connect(self,address)
    def block_ip_ex(self,address):
        if self.family in (socket.AF_INET,socket.AF_INET6): raise AssertionError('IPv4/IPv6 disabled in offline tests')
        return connect_ex(self,address)
    monkeypatch.setattr(socket.socket,'connect',block_ip)
    monkeypatch.setattr(socket.socket,'connect_ex',block_ip_ex)
