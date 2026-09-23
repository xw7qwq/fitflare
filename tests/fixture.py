"""Deterministic synthetic data; never copy production health data into tests."""
import json
from datetime import date, timedelta
from pathlib import Path


def make_fixture(profiles_dir):
    root = Path(profiles_dir)
    for profile in ('Demo', 'Hidden'):
        directory = root / profile
        for name in ('auth', 'cache', 'csv'):
            (directory / name).mkdir(parents=True, exist_ok=True)
        (directory / 'auth/client.json').write_text(json.dumps({'client_id': 'test-only', 'created_at': '2026-01-01T00:00:00'}))
        (directory / 'auth/tokens.json').write_text('{}')
        (directory / 'csv/example.csv').write_text('date,value\n2026-09-23,1\n')
        daily = [dict(date=str(date(2026, 8, 1) + timedelta(days=i)), sleep_hours=7+i%3/10,
                      sleep_score=80+i%10, steps=5000+i*20, hrv=50+i%5, rhr=60+i%4,
                      active_minutes=40, active_zone_minutes=20, calories_out=2100,
                      minutes_deep=70, minutes_rem=90, minutes_light=260) for i in range(55)]
        stats = [dict(key=key, label=label, latest=daily[-1][key], latest_date=daily[-1]['date'],
                      avg7=daily[-1][key], avg30=daily[0][key], unit=unit)
                 for key,label,unit in [('sleep_hours','睡眠时长','小时'), ('steps','步数','步'), ('hrv','HRV','ms'), ('rhr','静息心率','bpm'), ('sleep_score','睡眠得分','分')]]
        tables = {key: [] for key in ('sleep','activity','activity_logs','recovery','body','vitals','foods','devices','badges','alarms','endpoints')}
        tables['sleep'] = [dict(date=row['date'], score=row['sleep_score'], hours=row['sleep_hours'], deep=70, rem=90, light=260, awake=10) for row in reversed(daily)]
        tables['activity'] = list(reversed(daily))
        tables['recovery'] = list(reversed(daily))
        payload = dict(generated_at='2026-09-23T12:00:00', profile=dict(id=profile, display_name=profile),
                       overview=dict(latest_date=daily[-1]['date'], latest_sync_at='2026-09-23T12:00:00', recovery_score=70),
                       coverage={}, stats=stats, correlations=[], charts=dict(daily=daily,weekly=[],monthly=[]),
                       sections={key: {'metrics': []} for key in ('activity','body','vitals','lifestyle','account')},
                       tables=tables, snapshot_status={}, files={'tokens':'/private/test-only/tokens.json'})
        (directory / 'cache/dashboard.json').write_text(json.dumps(payload))
    return root


if __name__ == '__main__':
    import sys
    print(make_fixture(sys.argv[1]))
