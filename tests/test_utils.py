# -*- coding: utf-8 -*-
import asyncio

from recorder_core.common import clean_name
from src.utils import trace_error_decorator


def test_trace_error_decorator_catches_async_errors():
    @trace_error_decorator
    async def broken():
        raise ValueError('boom')

    assert asyncio.run(broken()) == {}


def test_trace_error_decorator_passes_through_results():
    @trace_error_decorator
    async def ok():
        return {'a': 1}

    @trace_error_decorator
    def sync_broken():
        raise ValueError('boom')

    assert asyncio.run(ok()) == {'a': 1}
    assert sync_broken() == []


def test_clean_name():
    assert clean_name('主播 名字?😀') == '主播_名字'
    assert clean_name('') == '空白昵称'
    assert clean_name('（测试）') == '(测试)'
