# Third-party notices

The root MIT license covers original project code, Skills and documentation. It does not relicense dependencies, embedded third-party JavaScript, or the data distributed under `data/LICENSE`.

## PyBroker

This project depends on `lib-pybroker==1.2.12`, copyright 2023 Edward West. Its distributed license is **Apache 2.0 with Commons Clause**, not unmodified Apache 2.0. The Commons Clause restricts selling the software as defined by that license.

The exact notice and license from the installed distribution are preserved in [PyBroker-LICENSE.txt](backtest/docs/licenses/PyBroker-LICENSE.txt). Upstream: <https://github.com/edtechre/pybroker>. The project MIT license does not remove this condition.

## Other dependencies and generated reports

Python dependencies are installed separately from `backtest/requirements.lock`; their own notices remain in the installed packages. [dependencies.json](backtest/docs/release/dependencies.json) records the installed package versions and license metadata for this release verification; it is an inventory, not a replacement for each package's license.

Generated HTML embeds Plotly.js through Plotly's standard HTML exporter. Retain its bundled copyright and MIT license notices when redistributing reports. Upstream: <https://github.com/plotly/plotly.js>.

`yfinance` is installed transitively by PyBroker. This release does not obtain its bundled data through Yahoo and does not invoke yfinance in its quickstart. Any later online provider integration has its own data-source terms; the project's data license covers only the data supplied by its rights holder with this release.
