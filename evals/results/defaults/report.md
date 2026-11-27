# SlopGuard evaluation: defaults

Included labels: 426; excluded labels: 69

## Run provenance

Tool version: 1\.0\.0; recording date: 2026\-11\-27T12:09:06\.303531Z; config: package defaults (SHA256 88d023650992dc419848948f652dc892f4a87eb958990a70eeb4dec00856a39d).
Labels SHA256: 42417eb7e3f68f133b4dbba8ac63be6adea6a1edbc341ffd23d55703a9499f51; registry recording SHA256: 3106580dfb961434a8b9e8c7a6605db2394be9bb14acca54db4473ffe6f11458.
Real PR file SHA256: ea63d0787c4e32e3ff913b5b83c02705e0f18da6a5c8368bddcb75b12bfeecb1; real PR registry SHA256: 193e30c507975bca7d4b2a0c61e67f226e2a8cd2628c9914b640994851202ed3.
Git commit and runtime are in run_info.json because they vary between runs.

## Verification coverage

Verified 491/495 labels; verification failures: 4.

| Class | Verified | Total | Failed |
| --- | ---: | ---: | ---: |
| hallucinated | 65 | 65 | 0 |
| legitimate | 150 | 150 | 0 |
| malicious | 100 | 100 | 0 |
| typosquat\_synthetic | 176 | 180 | 4 |

Verification failures:
- typosquat\_synthetic, phack: recording status is exists
- typosquat\_synthetic, pyacl: recording status is exists
- typosquat\_synthetic, vcrypt: recording status is exists
- typosquat\_synthetic, ztdata: recording status is exists
Labels are verified automatically against their primary sources (OSV records, the seeded generators, the popularity snapshot and recorded model outputs); they were not individually reviewed by a person.

## Metrics

| Threshold | TP | FP | TN | FN | Precision | Recall | FPR | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Flagged = warn or block | 276 | 1 | 149 | 0 | 0.9964 | 1.0000 | 0.0067 | 0.9982 |
| Flagged = block | 0 | 0 | 150 | 276 | n/a | 0.0000 | 0.0000 | n/a |

Precision = TP / (TP + FP); recall = TP / (TP + FN); FPR = FP / (FP + TN); F1 = 2 × precision × recall / (precision + recall).
Undefined metrics use `null` in JSON and `n/a` here when their denominator is zero.

## Per-category breakdown

| Category | Threshold | n | TP | FP | TN | FN | Precision | Recall | FPR | F1 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| lookalike\-legitimate | warn or block | 50 | 0 | 1 | 49 | 0 | 0.0000 | n/a | 0.0200 | n/a |
| lookalike\-legitimate | block only | 50 | 0 | 0 | 50 | 0 | n/a | n/a | 0.0000 | n/a |
| malicious\-package | warn or block | 100 | 100 | 0 | 0 | 0 | 1.0000 | 1.0000 | n/a | 1.0000 |
| malicious\-package | block only | 100 | 0 | 0 | 0 | 100 | n/a | 0.0000 | n/a | n/a |
| rank\-stratum\-1 | warn or block | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| rank\-stratum\-1 | block only | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| rank\-stratum\-2 | warn or block | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| rank\-stratum\-2 | block only | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| rank\-stratum\-3 | warn or block | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| rank\-stratum\-3 | block only | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| rank\-stratum\-4 | warn or block | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| rank\-stratum\-4 | block only | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| rank\-stratum\-5 | warn or block | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| rank\-stratum\-5 | block only | 20 | 0 | 0 | 20 | 0 | n/a | n/a | 0.0000 | n/a |
| synthetic\-deletion | warn or block | 37 | 37 | 0 | 0 | 0 | 1.0000 | 1.0000 | n/a | 1.0000 |
| synthetic\-deletion | block only | 37 | 0 | 0 | 0 | 37 | n/a | 0.0000 | n/a | n/a |
| synthetic\-insertion | warn or block | 49 | 49 | 0 | 0 | 0 | 1.0000 | 1.0000 | n/a | 1.0000 |
| synthetic\-insertion | block only | 49 | 0 | 0 | 0 | 49 | n/a | 0.0000 | n/a | n/a |
| synthetic\-separator\-change | warn or block | 26 | 26 | 0 | 0 | 0 | 1.0000 | 1.0000 | n/a | 1.0000 |
| synthetic\-separator\-change | block only | 26 | 0 | 0 | 0 | 26 | n/a | 0.0000 | n/a | n/a |
| synthetic\-substitution | warn or block | 26 | 26 | 0 | 0 | 0 | 1.0000 | 1.0000 | n/a | 1.0000 |
| synthetic\-substitution | block only | 26 | 0 | 0 | 0 | 26 | n/a | 0.0000 | n/a | n/a |
| synthetic\-transposition | warn or block | 38 | 38 | 0 | 0 | 0 | 1.0000 | 1.0000 | n/a | 1.0000 |
| synthetic\-transposition | block only | 38 | 0 | 0 | 0 | 38 | n/a | 0.0000 | n/a | n/a |

## Excluded rows

| Package | Reason |
| --- | --- |
| accelerate | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| asn1crypto | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| certifi | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| cryptography | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| datasets | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| djitellopy | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| easyocr | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| facenet\-pytorch | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| faster\-whisper | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| geopandas | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| google\-auth | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| google\-cloud\-vision | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| google\-genai | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| gpiozero | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| gradio | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| gspread | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| influxdb\-client | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| joblib | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| lgpio | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| librosa | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| matplotlib | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| minimalmodbus | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| mt\-940 | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| mt940 | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| numpy | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| obd | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| oletools | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| openai\-whisper | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| opencv\-python | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| opencv\-python\-headless | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| openpyxl | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| pandas | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| peft | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| phack | label marked exclude |
| Pillow | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| psxdata | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| psycopg | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| psycopg2\-binary | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| pyacl | label marked exclude |
| pydicom | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| pyHanko | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| pyhanko\-certvalidator | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| pymodbus | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| PyMuPDF | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| pyogrio | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| pypdfium2 | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| pyserial | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| pytesseract | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| python\-barcode | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| python\-dotenv | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| python\-escpos | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| rapidfuzz | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| reportlab | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| requests | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| safetensors | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| schedule | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| scikit\-learn | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| seaborn | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| shapely | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| simplefix | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| soundfile | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| torch | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| torchvision | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| transformers | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| trl | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| twilio | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| ultralytics | LLM\-suggested name exists on PyPI \(not a hallucination\) |
| vcrypt | label marked exclude |
| ztdata | label marked exclude |

## LLM-suggested package outcomes

Checked 65 names; 0 did not exist on PyPI (hallucination rate 0.0000); SlopGuard flagged 0 not-found names (warn or block).
0 additional lookups were inconclusive and are excluded from the rate denominator.

| Model | Checked | Not found | Hallucination rate | Flagged (warn/block) | Blocked |
| --- | ---: | ---: | ---: | ---: | ---: |
| ChatGPT Pro \(model 6\.2 Sol\), chatgpt\.com, web search setting off | 49 | 0 | 0.0000 | 0 | 0 |
| Gemini \(free, Auto\), gemini\.google\.com, search disabled by instruction | 49 | 0 | 0.0000 | 0 | 0 |
A name suggested by more than one model is counted once for each model; the overall line counts unique names.

## Attack-time scenario (as if freshly registered)

Synthetic metadata: first release 1 day(s) before recording, 1 release(s), and no source links.
New-package signals alone can cross the warn threshold, so expected-pass controls may be warned.

| Threshold | TP | FP | TN | FN | Precision | Recall | FPR |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Warn or block | 276 | 150 | 0 | 0 | 0.6479 | 1.0000 | 1.0000 |
| Block only | 0 | 0 | 150 | 276 | n/a | 0.0000 | 0.0000 |

## Historical pull request sample

Selection rule: most recent first\-parent commits on the default branch before the cutoff whose diff yields at least one dependency candidate; repositories/default branches: https://github\.com/psf/requests\.git \(main\), https://github\.com/pallets/flask\.git \(main\), https://github\.com/encode/httpx\.git \(master\), https://github\.com/fastapi/fastapi\.git \(master\), https://github\.com/pydantic/pydantic\.git \(main\), https://github\.com/psf/black\.git \(main\), https://github\.com/pytest\-dev/pytest\.git \(main\), https://github\.com/Textualize/rich\.git \(main\), https://github\.com/fastapi/sqlmodel\.git \(main\), https://github\.com/pallets/click\.git \(main\); per_repo=5; cutoff_date=2026\-11\-01; MAX_COMMITS_TO_INSPECT=5000.
Candidate-level FPR (flagged new dependencies / all new dependencies): 0/109 = 0.0000; block-only candidate rate: 0.0000.
PR-level warn/block rate: 0/50 = 0.0000; block-only PR rate: 0.0000.

### Repository inspection

| Repository | Commits inspected | Inspection limit reached |
| --- | ---: | --- |
| https://github\.com/Textualize/rich\.git | 234 | False |
| https://github\.com/encode/httpx\.git | 197 | False |
| https://github\.com/fastapi/fastapi\.git | 32 | False |
| https://github\.com/fastapi/sqlmodel\.git | 41 | False |
| https://github\.com/pallets/click\.git | 23 | False |
| https://github\.com/pallets/flask\.git | 97 | False |
| https://github\.com/psf/black\.git | 212 | False |
| https://github\.com/psf/requests\.git | 154 | False |
| https://github\.com/pydantic/pydantic\.git | 21 | False |
| https://github\.com/pytest\-dev/pytest\.git | 497 | False |

### Pull requests and candidates

| Repository | Base..head | Subject | Candidate names and decisions | Reason |
| --- | --- | --- | --- | --- |
| https://github\.com/Textualize/rich\.git | 245649b3e2d6e16aef315d95904df26bee7b8550..027a4727a5b8f8407109c01df5e24604352bbe50 | Merge pull request \#3350 from ofek/fix\-test\-warning | importlib\-metadata \(pass\) | — |
| https://github\.com/Textualize/rich\.git | b8975c5fd33c77b40d78c20803230083c10fd531..42bfda8b51f7bd253d04439aea36ff88122237fc | Merge pull request \#3226 from mgorny/no\_color | pytest \(pass\) | — |
| https://github\.com/Textualize/rich\.git | 21b3800894f6a81d3f68a0e34796be67e3ccd39f..e0c7e96b303e6f3e48818380527129fb21f453f3 | Merge pull request \#3783 from Textualize/live\-typing | typing\_extensions \(pass\) | — |
| https://github\.com/Textualize/rich\.git | 68224905f5dc7a3c765e04aba0460b75a95f5004..ea78218244cfa743c8cd06fc7808560233ac58df | Merge pull request \#2977 from Textualize/markdown\-tables | typing\_extensions \(pass\) | — |
| https://github\.com/Textualize/rich\.git | 53757bc234cf18977cade41a5b64f3abaccb0b85..f000c3149166cc2091b801b63b0a55e806c5d49b | Merge pull request \#3930 from Textualize/cell\-string | pytest \(pass\); wcwidth \(pass\) | — |
| https://github\.com/encode/httpx\.git | 1b7f39eb44304f76b5f402914a2d2fbf57dc24c6..280a89a4d13b65852b065b3e1aaa53d388946a88 | Support newer versions of httpcore \(\#2885\) | anyio \(pass\) | — |
| https://github\.com/encode/httpx\.git | 5b156dca7f3ec637b575708198f60a062109f141..2c49a151d270cc612f61c4094dbf281cbf2f63a4 | Pin CI version of \`click\` to resolve mypy error \(\#2769\) | click \(pass\) | — |
| https://github\.com/encode/httpx\.git | 7df47ce4d93a06f2c3310cd692b4c2336d7663ba..392dbe45f086d0877bd288c5d68abf860653b680 | Add support for zstd decoding \(\#3139\) | zstandard \(pass\) | — |
| https://github\.com/encode/httpx\.git | 3f76571d3429497c4ee4ecf5d8bc034b6f765957..8e36f2bc685dfbe43cd7503bc1c422a6ed6e05a5 | Introduce new \`SSLContext\` API & escalate deprecations\. \(\#3319\) | brotli \(pass\); brotlicffi \(pass\); zstandard \(pass\) | — |
| https://github\.com/encode/httpx\.git | 92e9dfb3997ab07353bbe98f9b5a6e69b7701c70..db9072f998b53ff66d50778bf5edee8e2cc8ede1 | Add URL parsing tests from WHATWG \(\#3188\) | pytest \(pass\) | — |
| https://github\.com/fastapi/fastapi\.git | e4c5d0293470f107bc0b4886f484a48c1315d091..2f6d43c6ecc9a392ea0fcb14c0b063e76b448b62 | ✨ Add support for optional query, header, and cookie parameter models \(\#16584\) | pydantic \(pass\); pytest \(pass\) | — |
| https://github\.com/fastapi/fastapi\.git | 68cc4cbb2e0600841b0d6bdd1cc29038b54a00e6..86274cc0acc9fc6d608e251c874105c94634bddf | ✨ Add support for optional unions of form models \(\#16586\) | pydantic \(pass\); pytest \(pass\) | — |
| https://github\.com/fastapi/fastapi\.git | ce6ee83e5411b98582e996e47edf7870418a68f4..91a335ce946c7a832da973f2acf69b8876594216 | 🐛 Apply each parameter model's \`extra\` configuration to sibling fields \(\#16594\) | pydantic\_core \(pass\) | — |
| https://github\.com/fastapi/fastapi\.git | addd0778e996cf886972b8024113d79f8d0a3580..e42ac0a13459086f42d96eab13748303bcfa78e7 | ✨ Add support for combining unions of form models \(\#16596\) | pydantic \(pass\); pytest \(pass\) | — |
| https://github\.com/fastapi/fastapi\.git | 69ddc2badf9da1123bba4d360225ae6b085985f6..f02ef4900bacc28942a051b95b9719c5002a7858 | 🐛 Restore default handling for omitted boolean form fields \(\#16605\) | pytest \(pass\) | — |
| https://github\.com/fastapi/sqlmodel\.git | 7fec3bcbdbca5b29b21ac4777e3fb341d8a7e4e1..01960be28ed852c5c118c5be163c91bb72f3cd91 | 🐛 Fix SQLAlchemy type handling in \`Field\` \(\#2095\) | pytest \(pass\); sqlalchemy \(pass\) | — |
| https://github\.com/fastapi/sqlmodel\.git | 43353a45817b30a51b7904d0b30e500bbbcef7ca..073023d84546490bd5dce86ce94fe6e8914db7c2 | 🏷️ Allow \`Discriminator\` type for \`discriminator\` parameter in \`Field\` \(\#1729\) | pydantic \(pass\) | — |
| https://github\.com/fastapi/sqlmodel\.git | a99fbcd820ffb339531408d0bd18c29f366cee13..7d1eba721e5e48b8bf9877007dbe803fd07539ba | ✨ Add support for SQLAlchemy 2\.1 \(\#2112\) | aiosqlite \(pass\); pytest \(pass\); typing\_extensions \(pass\) | — |
| https://github\.com/fastapi/sqlmodel\.git | 72bca1e492071cbd152d94fc5770f78130538af7..8da92357f55f1295c4e39072263106f5122ee34d | ✨ Add direct support for PostgreSQL, MySQL, and MariaDB, include in CI tests \(\#2103\) | pymysql \(pass\); sqlalchemy \(pass\); dirty\_equals \(pass\) | — |
| https://github\.com/fastapi/sqlmodel\.git | ff86856e04967945646b60293c43af1ba1b4188e..cc998aaa45f9d3bb76dbe20438c3ce2d449a0c73 | ✨ Use UTC datetimes by default \(\#2099\) | pydantic \(pass\); sqlalchemy \(pass\); alembic \(pass\); pytest \(pass\) | — |
| https://github\.com/pallets/click\.git | 7df2f82305f20f1611a9668b38d26933b648d807..333c28d79cd982990ee98eef61ec20ab1a4f38ba | Mark clearly functions private status\. Deprecated Python 2 utilities\. \(\#3695\) | pytest \(pass\) | — |
| https://github\.com/pallets/click\.git | 6aabf099bfdd4c1e75fe8d0e0d4241372b988ab1..3cbaa76b6014be7427d1ab06b4af40f01e7c278e | Merge stable into main \(\#3876\) | typing\_extensions \(pass\) | — |
| https://github\.com/pallets/click\.git | cfa01eeb7894a408af70b29d28c0b24f8680f9fb..5e906a8afb67242dffdd91404dbf31f469054b15 | \`click\.prompt\` typing clarifications / improvements \(\#3407\) | typing\_extensions \(pass\) | — |
| https://github\.com/pallets/click\.git | 36baa15ff831b939a22bc527cd76ce653ef6f66d..6aabf099bfdd4c1e75fe8d0e0d4241372b988ab1 | Stable \(\#3851\) | pytest \(pass\) | — |
| https://github\.com/pallets/click\.git | 2103e157683c5e4cadc8ee1838df526a54bde9a4..e1fd5946ab26aaf372009eaff1acf947140b40fb | Add support of \`pathlib\.Path\` to \`edit\` \(\#3781\) | typing\_extensions \(pass\) | — |
| https://github\.com/pallets/flask\.git | 330123258e8c3dc391cbe55ab1ed94891ca83af3..adf363679da2d9a5ddc564bb2da563c7ca083916 | merge app and request context \(\#5812\) | typing\_extensions \(pass\) | — |
| https://github\.com/pallets/flask\.git | f61172b8dd3f962d33f25c50b2f5405e90ceffa5..b78b5a210bde49e7e04b62a2a4f453ca10e0048c | Merge branch 'stable' | roman\-numerals\-py \(pass\) | — |
| https://github\.com/pallets/flask\.git | 11c45eeba3b5bcf06b84f4e864862f1019a3faa3..bc143499cf1137a271a7cf75bdd3e16e43ede2f0 | Merge branch 'stable' | markupsafe \(pass\); ruff \(pass\); tox \(pass\); tox\-uv \(pass\); pallets\-sphinx\-themes \(pass\); sphinx \(pass\); sphinx\-tabs \(pass\); sphinxcontrib\-log\-cabinet \(pass\); sphinx\-autobuild \(pass\); gha\-update \(pass\); pre\-commit \(pass\); pre\-commit\-uv \(pass\); greenlet \(pass\); pytest \(pass\); cryptography \(pass\); mypy \(pass\); pyright \(pass\); types\-contextvars \(pass\); types\-dataclasses \(pass\) | — |
| https://github\.com/pallets/flask\.git | 2fec0b206c6e83ea813ab26597e15c96fab08be7..bca18041b055102857a7e8a12f8694fddda9571b | Merge branch '3\.0\.x' | sphinx\-notfound\-page \(pass\) | — |
| https://github\.com/pallets/flask\.git | 4e6384da325f5c328526b998d199c6f957f90ea8..eeb5f95a0f24abc5207159cc922cdda8d779f4f6 | Merge branch '3\.0\.x' | importlib\-metadata \(pass\); tomli \(pass\); zipp \(pass\); exceptiongroup \(pass\); greenlet \(pass\); pytz \(pass\) | — |
| https://github\.com/psf/black\.git | 30664d8f17b29658aa72e44a1bfe23eede12db99..315a796590d112d15cf74b941ca83b53bb331068 | Fix GitHub Action crash on dependency\-group includes \(\#5479\) | pytest \(pass\) | — |
| https://github\.com/psf/black\.git | 77980472e82b5e733a48a3bd08c59beb6a9dba65..9888d14643ac414d7a24bc3e082af1c7cbe06efa | Fix crash on \`\# fmt: skip\` after a multi\-line \`from\` import \(\#5584\) | a \(pass\); c \(pass\); d \(pass\); k \(pass\); m \(pass\) | — |
| https://github\.com/psf/black\.git | fe241ccc443acbabbd75c8240d2fbc06ba4e52d6..ac1f71563aeccf73cf13ece00f231d9d6c66a514 | Action: validate required\-version better \(\#5510\) | pytest \(pass\) | — |
| https://github\.com/psf/black\.git | 158aa3c0af7d6c1fd189aa58e45b671bb9a4186b..b7f9e86ea23d22c9d27ae964e07d1855ee91069f | Avoid OSError: AF\_UNIX path too long for multiprocessing manager when running \-\-diff \(\#2105\) \(\#5513\) | pytest \(pass\) | — |
| https://github\.com/psf/black\.git | 0a6396ba56d2d7fac4ce4fef5ea15c97e82b299b..d7587ce9f52f36eb41bc505e529540fd8cbdcd3e | Validate BLACK\_NUM\_WORKERS values \(\#5211\) | click \(pass\) | — |
| https://github\.com/psf/requests\.git | 389eea58dfb2f2ee096421a812e3af29c0298951..4443b1a847b190010c2972a658924b98b5db6360 | Fix unintended test extra \(\#7306\) | trustme \(pass\) | — |
| https://github\.com/psf/requests\.git | 8f6cda9969f4a98c45ea2922f6bcbefc02256202..561e4b6889f53584c39a67f8794c53a414f68481 | Add inline types to Requests \(\#7272\) | pyright \(pass\); typing\_extensions \(pass\) | — |
| https://github\.com/psf/requests\.git | 15e1f17bb6f3f978787dd23da0268388148bc3d5..79b74ef704dc0d804937c0d015c5e9c3b02b79bd | Merge pull request \#6757 from nateprewitt/urllib\-1\.x\-tests | urllib3 \(pass\) | — |
| https://github\.com/psf/requests\.git | a389aaad453ac4c0b9f8af4a72429de164810654..cb2c800518423e0042789ab09f8f6e719428a444 | Migrate build system to PEP 517 \(\#7012\) | setuptools \(pass\); charset\_normalizer \(pass\); idna \(pass\); urllib3 \(pass\); certifi \(pass\); PySocks \(pass\); chardet \(pass\); pytest\-httpbin \(pass\); pytest\-cov \(pass\); pytest\-mock \(pass\); pytest\-xdist \(pass\); pytest \(pass\) | — |
| https://github\.com/psf/requests\.git | 3be097d450ee4ca8a2f9c1a3a58eb61ed19936b0..e50e5945294f79ee9ab4ec69de24d14f9b26a7ae | Improve static typing of 3rd party imports  \(\#7496\) | cryptography \(pass\); PyOpenSSL \(pass\); chardet \(pass\) | — |
| https://github\.com/pydantic/pydantic\.git | 2a58e7cb614787e532957562c2b6f9cc4a1f935b..1672be5360805c06307c4ba91b73a65860da4834 | Add fresh\-interpreter library startup benchmarks \(\#14112\) | pytest \(pass\) | — |
| https://github\.com/pydantic/pydantic\.git | 2a62f7a904734206cb029b94e25b9b968158aa63..2a58e7cb614787e532957562c2b6f9cc4a1f935b | Benchmark nested JSON model validation and fallback \(\#14128\) | pytest \(pass\); pytest\_benchmark \(pass\) | — |
| https://github\.com/pydantic/pydantic\.git | 91c44cd1bfc8c7cb909cefa8395e0a720957f1d1..69e9ca4658a6471cadae5216b44ebfb4fde040f3 | Remove functionality deprecated during the 2\.x timeline \(\#14158\) | griffe \(pass\) | — |
| https://github\.com/pydantic/pydantic\.git | bb7e4d733632e9bcb0312c53da05efb7a1d71cb3..779361fe0c4ed5be8ea31e136b4353ae2e5fa673 | Improve performance of \`model\_fields\_set\` \(\#12924\) | pytest \(pass\); pydantic\_core \(pass\) | — |
| https://github\.com/pydantic/pydantic\.git | 27caa1e4dea73c6cd50e48cbbba315d817c1cf0a..dd0c36a4be235522e34659ba372c59b43b55f33e | Add JSON model streaming fallback benchmarks \(\#14132\) | pytest \(pass\); pytest\_benchmark \(pass\) | — |
| https://github\.com/pytest\-dev/pytest\.git | ef052d0bc481af97afb3fbd2aae24d8abce08604..344c23787cdb3431dcc441b8b63ee9950f04b921 | Accept type expressions in Parser\.addini\(type=\.\.\.\) \(\#14751\) | typing\_extensions \(pass\) | — |
| https://github\.com/pytest\-dev/pytest\.git | 6a47389c7813ec7ed395c0279c4b7685fde9f95a..3fe80c714fe18f2926b42997b55b05e6b2e90ab0 | Merge pull request \#13918 from bluetech/restore\-pytest\-asyncio | pytest\-asyncio \(pass\) | — |
| https://github\.com/pytest\-dev/pytest\.git | b2af87e54026af746e49664becb33e6823242bfc..4253f98a78e44ff693007edc439885d6f656eed6 | Merge pull request \#13877 from bluetech/deprecate\-parametrize\-iterators | typing\_extensions \(pass\) | — |
| https://github\.com/pytest\-dev/pytest\.git | 5eaad82074c08b63ee6667d9760d795ea16e73f9..79392133b63bc79efde860eb94901108b88cfcd6 | Integrate pytest\-subtests \(\#13738\) | pluggy \(pass\); typing\_extensions \(pass\) | — |
| https://github\.com/pytest\-dev/pytest\.git | d07c5d4e3956cb6b8ff7f9f49c0bc4d819a15349..849fbda3ce87e3829b30d005979bee59928c01ff | Merge pull request \#14122 from RonnyPfannschmidt/enable\-uv | pytest \(pass\) | — |

These are proxy false-positive rates: merged history is not a human-verified ground-truth label set.

## Limitations

This is a preliminary evaluation over a curated set of a few hundred names. Synthetic typosquats are not equivalent to real attacks, label coverage is incomplete, and registry state can change after recording. Metrics should guide review, not be read as a malware verdict.
