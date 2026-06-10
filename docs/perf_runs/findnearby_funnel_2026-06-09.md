# find_nearby — AWS in-region profile + candidate funnel (2026-06-09)

Real `aws` worker (x86_64, 2 GB), **uncached** (`PYNIGHTSKY_NO_CACHE=1`), warm container (steady-state; cold-start phases like PAD-US load amortised). radius=60 mi.

All optimizations shipped to date are in effect (parallel geocode, columnar PAD-US, vectorised dome detection).


## Per-phase wall time (ms)

| Phase | San Diego | Sedona | Knolls | Roswell | Atlantic City |
|---|---|---|---|---|---|
| origin lookup | 0 | 56 | 332 | 117 | 157 |
| viirs window read | 131 | 151 | 253 | 230 | 406 |
| falchi window read | 163 | 112 | 198 | 280 | 250 |
| extract dark candidates | 297 | 227 | 247 | 221 | 219 |
| cluster + band select | 18 | 30 | 59 | 33 | 28 |
| light dome detection | 188 | 184 | 199 | 206 | 204 |
| best-available numpy pass | 0 | 0 | 0 | 0 | 0 |
| origin settlement | 81 | 77 | 73 | 99 | 60 |
| padus index load | 0 | 0 | 0 | 0 | 0 |
| dome naming (geocode) | 0 | 1451 | 1417 | 0 | 0 |
| jit geocode candidates | 297 | 85 | 1176 | 242 | 70 |
| drive times (aws) | 338 | 182 | 775 | 577 | 610 |
| TOTAL (sum of phases) | 1513 | 2555 | 4729 | 2005 | 2004 |

## Candidate funnel (counts)

| Stage | San Diego | Sedona | Knolls | Roswell | Atlantic City |
|---|---|---|---|---|---|
| origin Bortle | B9 | B7 | B1 | B8 | B9 |
| extract_raw (dark px, capped 500) | 421 | 504 | 504 | 420 | 504 |
| → clusters (merge 1mi) | 71 | 132 | 231 | 154 | 100 |
| → band_selected (cap 60) | 51 | 50 | 57 | 50 | 60 |
| → **results_final** (cap 10) | 10 | 7 | 10 | 10 | 10 |
| domes_raw (bright blobs) | 272 | 135 | 119 | 751 | 734 |
| → domes_pass_filter | 0 | 35 | 119 | 0 | 0 |
| → **domes_final** (cap 10) | 0 | 10 | 10 | 0 | 0 |

## Surfaced coordinates


### San Diego, CA  (32.7157, -117.1611)  — origin B9, SQM 16.2

**Dark-sky results:**

| name | B | SQM | dist mi | dir | lat | lon |
|---|---|---|---|---|---|---|
| Anza-Borrego Desert State Park | 2 | 21.70 | 46.8 | ENE | 32.9744 | -116.41662 |
| Vallecito County Park | 2 | 21.75 | 50.0 | ENE | 32.98692 | -116.3624 |
| Julian, CA | 2 | 21.70 | 46.7 | ENE | 32.95354 | -116.40827 |
| Torrey Pines State Reserve | 3 | 21.47 | 16.1 | NNW | 32.93267 | -117.26328 |
| Rancho Jamul Ecological Reserve | 3 | 21.43 | 16.5 | E | 32.68649 | -116.87957 |
| Hollenbeck Canyon Wildlife Area | 3 | 21.42 | 20.0 | E | 32.71153 | -116.81701 |
| Cabrillo National Monument | 3 | 21.33 | 5.5 | SW | 32.66563 | -117.23409 |
| San Vicente Reservoir | 3 | 21.44 | 20.0 | NE | 32.92016 | -116.91711 |
| Tijuana | 3 | 21.40 | 16.3 | S | 32.48204 | -117.12148 |
| Goodan Ranch Sycamore Canyon Preserve | 3 | 21.30 | 15.9 | NE | 32.89929 | -116.99635 |

### Sedona, AZ  (34.8697, -111.761)  — origin B7, SQM 18.7

**Dark-sky results:**

| name | B | SQM | dist mi | dir | lat | lon |
|---|---|---|---|---|---|---|
| Coconino National Forest | 1 | 22.00 | 19.0 | E | 34.85718 | -111.42536 |
| Kaibab National Forest | 1 | 22.03 | 40.0 | NNW | 35.42465 | -111.96322 |
| Flagstaff, AZ | 1 | 22.06 | 50.0 | N | 35.58321 | -111.90901 |
| State Trust Land | 1 | 22.00 | 30.0 | ENE | 35.0074 | -111.25859 |
| Leuppx, AZ | 1 | 22.03 | 50.0 | NE | 35.36206 | -111.11266 |
| Williams, AZ | 1 | 22.05 | 50.0 | NNW | 35.47472 | -112.24673 |
| Ash Fork, AZ | 1 | 22.03 | 50.0 | WNW | 35.10336 | -112.59696 |

**Light domes:**

| name | B | dist mi | dir | lat | lon |
|---|---|---|---|---|---|
| Flagstaff, AZ | 9 | 35.4 | NE | 35.17013 | -111.25442 |
| Prescott, AZ | 9 | 43.5 | WSW | 34.60266 | -112.4552 |
| Phoenix, AZ | 9 | 78.8 | SSW | 33.77648 | -112.15501 |
| Peoria, AZ | 9 | 83.8 | SSW | 33.7139 | -112.20504 |
| Scottsdale, AZ | 9 | 86.0 | S | 33.63044 | -111.90901 |
| Surprise, AZ | 9 | 93.5 | SSW | 33.61375 | -112.37182 |
| Luke Air Force Base, AZ | 9 | 98.4 | SSW | 33.53448 | -112.35931 |
| Litchfield Park, AZ | 9 | 100.5 | SSW | 33.50944 | -112.38432 |
| Buckeye, AZ | 9 | 109.1 | SSW | 33.43433 | -112.55527 |
| Queen Creek, AZ | 9 | 109.9 | S | 33.28829 | -111.55462 |

### Knolls, UT  (40.7286, -113.2987)  — origin B1, SQM 22.0

**Dark-sky results:**

| name | B | SQM | dist mi | dir | lat | lon |
|---|---|---|---|---|---|---|
| The State of Utah School and Institutional Trust Lands Administration 4343 | 1 | 22.04 | 10.0 | WNW | 40.77867 | -113.47799 |
| State Trust Lands Wendover Block | 1 | 22.01 | 20.0 | W | 40.69522 | -113.67813 |
| West Wendover, NV | 1 | 22.05 | 50.0 | WSW | 40.39062 | -114.14096 |
| Montello, NV | 1 | 22.03 | 50.0 | WNW | 41.04154 | -114.16181 |
| The State of Utah School and Institutional Trust Lands Administration 4318 | 1 | 22.02 | 1.0 | WNW | 40.73695 | -113.31538 |
| The State of Utah School and Institutional Trust Lands Administration 4049 | 1 | 22.07 | 50.0 | SW | 40.23206 | -113.99085 |
| Grouse Creek, UT | 1 | 22.01 | 20.0 | NNE | 41.00816 | -113.19863 |
| The State of Utah School and Institutional Trust Lands Administration 4474 | 1 | 22.06 | 40.0 | NNW | 41.23348 | -113.67396 |
| Dugway, UT | 1 | 22.02 | 20.0 | SSE | 40.45738 | -113.16527 |
| Great Salt Lake | 1 | 22.01 | 40.0 | NNE | 41.26269 | -113.00266 |

**Light domes:**

| name | B | dist mi | dir | lat | lon |
|---|---|---|---|---|---|
| West Wendover, NV | 9 | 39.5 | W | 40.73695 | -114.0534 |
| Tooele, UT | 9 | 51.8 | ESE | 40.53666 | -112.34386 |
| Salt Lake City, UT | 9 | 63.5 | E | 40.80371 | -112.08952 |
| West Jordan, UT | 9 | 66.9 | E | 40.57421 | -112.03948 |
| West Valley City, UT | 9 | 68.9 | E | 40.67018 | -111.98528 |
| Herriman, UT | 9 | 69.2 | ESE | 40.52414 | -112.00612 |
| South Jordan, UT | 9 | 70.1 | E | 40.55335 | -111.98111 |
| Clearfield, UT | 9 | 72.4 | ENE | 41.09161 | -111.99779 |
| Layton, UT | 9 | 74.6 | ENE | 41.06241 | -111.93941 |
| Eagle Mountain, UT | 9 | 74.6 | ESE | 40.26962 | -112.01446 |

### Roswell, GA  (34.0232, -84.3616)  — origin B8, SQM 17.4

**Dark-sky results:**

| name | B | SQM | dist mi | dir | lat | lon |
|---|---|---|---|---|---|---|
| McGraw Ford Wildlife Management Area | 3 | 21.32 | 20.0 | N | 34.31111 | -84.3199 |
| Ball Ground, GA | 3 | 21.32 | 17.7 | N | 34.27773 | -84.32407 |
| Field's Landing Park | 3 | 21.31 | 18.0 | NW | 34.21514 | -84.57427 |
| Allatoona Wildlife Management Area | 3 | 21.32 | 17.1 | NW | 34.17759 | -84.59512 |
| Victoria Campground | 3 | 21.32 | 17.4 | NW | 34.16507 | -84.6118 |
| Allatoona Recreation Area | 3 | 21.39 | 16.9 | WNW | 34.13586 | -84.62431 |
| Ficklen Church Rd. | 3 | 21.30 | 18.0 | NW | 34.19428 | -84.59929 |
| Sidney Lanier Recreation Area | 3 | 21.31 | 20.1 | ENE | 34.16924 | -84.05719 |
| Jasper, GA | 3 | 21.45 | 30.0 | N | 34.45298 | -84.28654 |
| Dallas, GA | 3 | 21.56 | 30.0 | W | 34.0691 | -84.88285 |

### Atlantic City, NJ  (39.3643, -74.4229)  — origin B9, SQM 15.6

**Dark-sky results:**

| name | B | SQM | dist mi | dir | lat | lon |
|---|---|---|---|---|---|---|
| Bowers Beach | 2 | 21.87 | 56.1 | WSW | 39.06387 | -75.39631 |
| Mispillion Marine Reserve | 2 | 21.71 | 56.2 | WSW | 38.9387 | -75.3171 |
| Absecon Wildlife Management Area | 3 | 21.32 | 2.6 | N | 39.40185 | -74.42082 |
| Clarks Mill Pond | 3 | 21.32 | 11.7 | NNW | 39.51869 | -74.51253 |
| Port Republic Wildlife Management Area | 3 | 21.33 | 10.0 | NNW | 39.50617 | -74.4625 |
| Edwin B. Forsythe National Wildlife Refuge | 3 | 21.58 | 10.4 | N | 39.51451 | -74.43332 |
| Tuckahoe Wildlife Management Area | 3 | 21.33 | 11.3 | WSW | 39.33092 | -74.62925 |
| Great Egg Harbor River Wildlife Management Area | 3 | 21.55 | 20.0 | WNW | 39.43106 | -74.78767 |
| Egg Harbor City, NJ | 3 | 21.54 | 20.0 | NNW | 39.623 | -74.59174 |
| Cape May National Wildlife Refuge | 3 | 21.51 | 20.0 | WSW | 39.21826 | -74.74598 |
