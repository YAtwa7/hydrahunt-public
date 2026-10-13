"""DorkLab — self-evolving search dork engine.

A dork is one Google-dork-syntax query run against DuckDuckGo's no-JS
endpoint. Fitness is the EMA of `valid_results / total_results`.

The invariant that keeps the population alive: an infrastructure failure
(CAPTCHA, timeout, 403) marks the *run* failed and leaves the dork's
fitness untouched. Scoring a blocked run as zero would systematically
kill the dorks that happen to hit a rate limit, which is the whole
population.
"""
