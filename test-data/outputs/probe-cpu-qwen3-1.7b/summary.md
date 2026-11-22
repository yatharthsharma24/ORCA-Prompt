# Test run: qwen3:1.7b (2026-11-22 21:11)

Passed 0/1. Settings overrides: {'llm_critic': 'false', 'max_plan_steps': '2', 'max_react_iterations': '4', 'num_ctx': '6144', 'num_thread': '2'}.

| Test no. | Input (short) | Type | Expected output | Actual output (short) | Pass / Fail |
|---|---|---|---|---|---|
| T09 | Kerala mein oil sardine ki landings 2016 mein kitni thi? | Edge | Understands the Hinglish question and gives Kerala oil sardine landings for 2016 (48,670 t), cited. | - Kerala mein oil sardine landings in 2016 were likely low, reflecting the decline trend observed in the mid-2010s, as indicated by a -87.547% percent change from 2013 to 2016 [E1]. The data is not recorded in the D-FIS… [verifier FAIL 0/1] (verifier failed; none of ['48,670', '48670'] in answer) | Fail |
