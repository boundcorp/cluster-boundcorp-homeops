import sys,datetime as dt
START,CONT=0.25,0.20
def run(off_min):
    on=False;low=0;cycles=[];s=None;last_active=None
    for line in open('vib1m.csv'):
        t,m,n=line.split();t=int(t);m=float(m)
        if not on:
            if m>START: on=True;s=t;low=0;last_active=t
        else:
            if m>=CONT: low=0;last_active=t
            else:
                low+=1
                if low>=off_min: on=False;cycles.append((s,last_active+60));
    if on: cycles.append((s,None))
    return cycles
f=lambda t:dt.datetime.fromtimestamp(t,dt.timezone(dt.timedelta(hours=-8))).strftime('%a %m-%d %H:%M') if t else 'ongoing'
for off in (10,12):
    c=run(off);print(f'off delay {off} min: {len(c)} cycles')
    for s,e in c: print('  ',f(s),'->',f(e),(round((e-s)/60) if e else ''),'min')
# old logic: count of 'on' transitions
n=sum(1 for l in open('run_states.csv') if l.split()[1]=='on');print('old logic on-transitions in recorder:',n)
