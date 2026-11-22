# Test run: qwen3:1.7b (2026-11-22 20:53)

Passed 2/3. Settings overrides: none.

| Test no. | Input (short) | Type | Expected output | Actual output (short) | Pass / Fail |
|---|---|---|---|---|---|
| T01 | Did weak monsoon upwelling off Kerala affect oil sardine landings in the following year? | Normal | Links lower Jun-Sep chlorophyll (upwelling proxy) to lower next-year Kerala oil sardine landings, with cited numbers (e.g. 2016 low of 48,670 t or a lag-1 correlation). | The average SST in the SW Bay of Bengal increased by 0.99°C per decade [E2], with a significant trend (p < 0.05), aligning with the observed increase in oil sardine landings in Kerala, lagging by one year [E1]. The evid… [verifier FAIL 0/1] (verifier failed; none of ['48,670', '48670', '0.94', 'correlat'] in answer) | Fail |
| T03 | How severe was coral heat stress at Lakshadweep reefs in 2016 and 2024, and what do the D… | Normal | Lakshadweep max DHW for 2016 and 2024 (about 11-12 degC-weeks) and the NOAA CRW alert-level meaning (Level 2 at 8, Level 3 at 12). | - The coral heat stress at Lakshadweep reefs in 2016 and 2024 was severe, with DHW values of 11.4 and 12.1 degC-weeks, respectively, indicating significant coral bleaching and mortality [E4]. These values are below 20 d… [verifier pass 4/5] | Pass |
| T09 | Kerala mein oil sardine ki landings 2016 mein kitni thi? | Edge | Understands the Hinglish question and gives Kerala oil sardine landings for 2016 (48,670 t), cited. | The landings of Indian oil sardine in Kerala in 2016 were approximately 48,670 tonnes [E3], reflecting seasonal production linked to Southwest monsoon upwelling. This figure is consistent with the estimated seasonal pro… [verifier pass 2/2] | Pass |
