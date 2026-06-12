import json, sys
d = json.load(sys.stdin)
print(f"{d['summary']['requestsPerSec']:.0f}\t{d['latencyPercentiles']['p99']*1000:.2f}")
