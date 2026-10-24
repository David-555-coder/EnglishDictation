"""Record coverage and full glosses, not a made-up semantic accuracy score."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import statistics
import time

from tools.translation_prototype.dictionary import Dictionary


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--samples')
    args=parser.parse_args();store=Dictionary(args.database)
    cases=json.loads((Path(args.samples) if args.samples else Path(__file__).with_name('dictionary_samples.json')).read_text(encoding='utf-8'))
    rows=[]
    for category,words in cases.items():
        for text in words:
            start=time.perf_counter();result=store.lookup(text);elapsed=(time.perf_counter()-start)*1000
            row={'category':category,'query':text,'match':result.match,
                 'entries':[asdict(e) for e in result.entries],'milliseconds':round(elapsed,3)}
            rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    times=sorted(row['milliseconds'] for row in rows)
    summary={'queries':len(rows),'matched':sum(bool(row['entries']) for row in rows),
             'median_ms':statistics.median(times),'p95_ms':times[int(len(times)*.95)-1],
             'first_lookup_ms':rows[0]['milliseconds'],
             'by_category':{category:{'queries':len(words),'matched':sum(row['category']==category and bool(row['entries']) for row in rows)} for category,words in cases.items()}}
    report={'normal_learning_database_opened':False,'network_requests':0,'summary':summary,'rows':rows}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
