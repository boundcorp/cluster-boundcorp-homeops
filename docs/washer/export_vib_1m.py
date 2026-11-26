import sqlite3
c=sqlite3.connect('file:/config/home-assistant_v2.db?mode=ro',uri=True)
rows=c.execute("select cast(last_updated_ts/60 as int) b, avg(cast(state as real)), count(*) from states where metadata_id=3 and state not in ('unknown','unavailable','') and last_updated_ts>1794744000 group by b order by b").fetchall()
for b,a,n in rows: print(b*60,round(a,4),n)
