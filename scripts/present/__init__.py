"""`scripts/present` — the generator for devrc's single-file explainer page.

Four modules, one direction of dependency:

    measure.py   takes every number at build time; owns `UNMEASURED`
    content.py   the prose and the hand-authored inline SVG; owns NO numbers
    sanitize.py  `--sanitize`: swaps real identifiers for synthetic stand-ins,
                 and WITHHOLDS columns a measurer declared to hold harvested
                 human prose — substitution cannot redact a sentence
    render.py    assembles one self-contained HTML file
    generate.py  the CLI, and the only place that decides a build has failed

🔴 `content.py` CONTAINS NO NUMBERS ON PURPOSE. Every quantity on the page comes
from `measure.py`. The moment prose here carries a count, it starts aging, and
this repo has measured its own prose false in both directions more than once.
"""
