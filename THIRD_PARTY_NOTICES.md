# Third-party notices

## Hadith detection engine

`hadith_engine/` is a vendored copy of
[`RayenFekih/hadith-detector`](https://github.com/RayenFekih/hadith-detector),
licensed under the MIT License. The full licence text is in
[`hadith_engine/LICENSE`](hadith_engine/LICENSE).

Copyright (c) 2026 Mohamed Rayen Fekih.

Local changes: package renamed from `hadith_detector` to `hadith_engine`,
version lookup made local, unused MCP subpackage removed, and the index builder
adapted to this repository's layout and Python 3.12.

## Hadith text data

The Arabic hadith texts are derived from
[`mhashim6/Open-Hadith-Data`](https://github.com/mhashim6/Open-Hadith-Data),
pinned at commit `1515f6cba21efed20d8916bf55acef1dffa0d2d5`.

That project is made available under the Open Database License (ODbL) 1.0:

- <http://opendatacommons.org/licenses/odbl/1.0/>

Any rights in individual contents of the database are licensed under the
Database Contents License (DbCL) 1.0:

- <http://opendatacommons.org/licenses/dbcl/1.0/>

Only the narration column is redistributed in the generated index; the
*Tafseel* commentary is not. The data is a digitisation, not a scholarly
critical edition. Hadith numbers follow the source edition and are not
identical to sunnah.com numbering. No authenticity grade is implied by
inclusion in the corpus.

## Quran detection

`quran-detector` is installed from PyPI and is not vendored here. See its
package metadata for its licence and data notices.

## YouTube transcript import

YouTube captions are fetched with
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp), installed from PyPI and not
vendored here. `yt-dlp` is released under the Unlicense; see the project for
the full notice. The importer only reads caption tracks and does not download
video or audio.
