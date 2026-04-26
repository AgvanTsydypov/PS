Each parent Event houses an array of child Markets. The system parses this array to isolate the cryptographic identifiers required for behavioral extraction.

* **Micro-Dust Filter:** Nested markets registering `< $1,000` in total volume are automatically purged from the pipeline.
* **Relational Mapping:** Extracted Markets are mapped to their parent Event, creating a clean topological structure for the next processing layer.