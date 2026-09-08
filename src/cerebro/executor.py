"""Mandatory bounded, external-access-disabled read-only DuckDB phases."""
from __future__ import annotations
import threading
import time
from pathlib import Path
import duckdb
from .query_models import CompiledQuery,QueryResult
from .provenance import QueryFault

class QueryTimeout(QueryFault):
    def __init__(self,phase='execution'): super().__init__(phase+'_timeout')

class DuckDBExecutor:
    def __init__(self,database_path: Path | str,timeout_seconds=30,explain_timeout_seconds=30,
                 memory_limit='512MB',threads=2,max_temp_directory_size='1GB'):
        if timeout_seconds<=0 or explain_timeout_seconds<=0 or threads<1: raise ValueError('invalid_engine_limits')
        self.timeout_seconds=timeout_seconds;self.explain_timeout_seconds=explain_timeout_seconds
        self.connection=duckdb.connect(str(database_path),read_only=True,config={
            'enable_external_access':False,'allow_unsigned_extensions':False,'memory_limit':memory_limit,
            'threads':threads,'max_temp_directory_size':max_temp_directory_size})
        self.effective_limits={'memory_limit':memory_limit,'threads':threads,'max_temp_directory_size':max_temp_directory_size,
                               'execution_seconds':timeout_seconds,'explain_seconds':explain_timeout_seconds}
        self._lock=threading.Lock()

    def _run(self,query,max_rows,phase,remaining_ms=None):
        if type(query) is not CompiledQuery: raise QueryFault('uncompiled_query')
        if not 1<=max_rows<=1000: raise QueryFault('invalid_row_cap')
        seconds=self.explain_timeout_seconds if phase=='explain' else self.timeout_seconds
        if remaining_ms is not None: seconds=min(seconds,remaining_ms/1000)
        if seconds<=0: raise QueryFault('budget_exceeded')
        params={str(p.position):p.value for p in query.parameters}
        with self._lock:
            timed_out=threading.Event()
            def interrupt():
                timed_out.set();self.connection.interrupt()
            watchdog=threading.Timer(seconds,interrupt);watchdog.daemon=True
            started=time.monotonic();watchdog.start()
            try:
                cursor=self.connection.execute(('EXPLAIN ' if phase=='explain' else '')+query.sql,params)
                if phase=='explain':
                    cursor.fetchall(); result=None
                else:
                    rows=cursor.fetchmany(max_rows+1)
                    description=cursor.description or ()
                    result=QueryResult(columns=tuple(x[0] for x in description),column_types=tuple(str(x[1]) for x in description),
                                       rows=tuple(tuple(x) for x in rows[:max_rows]),row_count=min(len(rows),max_rows),
                                       truncated=len(rows)>max_rows,elapsed_ms=int((time.monotonic()-started)*1000))
            except duckdb.InterruptException:
                raise QueryTimeout(phase) from None
            except duckdb.Error:
                raise QueryFault('explain_failed' if phase=='explain' else 'execution_error') from None
            finally:
                watchdog.cancel();watchdog.join()
            if timed_out.is_set() or time.monotonic()-started>seconds: raise QueryTimeout(phase)
            return result

    def explain(self,query,remaining_ms=None): return self._run(query,1,'explain',remaining_ms)
    def __call__(self,query,max_rows,remaining_ms=None): return self._run(query,max_rows,'execution',remaining_ms)
    def close(self):
        with self._lock: self.connection.close()
