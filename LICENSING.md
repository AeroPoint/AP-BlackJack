# Licensing

This project is **dual-licensed**.

## Open source: AGPL-3.0-only

Everything in this repository is available under the
[GNU Affero General Public License v3.0](LICENSE). In short:

- You may use, study, modify and share it, including commercially.
- If you **distribute** it, modified or not, you must make the complete
  corresponding source available under the AGPL-3.0.
- If you **modify** it and let people use your version **over a network**, for
  example by hosting the API or a web app built on it, you must offer those users
  the source of your modified version.

For personal use, study, research, teaching and open-source projects, this
usually means nothing more than "keep the licence and share your changes".

## Commercial licence

If you want to use this software in a way that does not meet the AGPL's terms,
for example in a closed-source product or in a hosted service whose source you
do not want to publish, a commercial licence is available from the copyright
holder.

To ask about one, contact [@AeroPoint on GitHub](https://github.com/AeroPoint).
Please do not put commercial or confidential details in a public issue.

## Contributions

Contributions are accepted under the AGPL-3.0-only. They also carry a grant that
lets the copyright holder include them in the commercial licence. The terms are
in [CONTRIBUTING.md](CONTRIBUTING.md#licensing-of-contributions), and the
reasoning is in
[ADR-0008](markdown/adr/ADR-0008-project-licence.md).

## Dependencies

Every dependency must be permissively licensed (MIT, BSD, Apache-2.0, PSF, ISC,
MPL-2.0). A copyleft dependency would make the commercial licence impossible to
grant. `scripts/check_licenses.py` enforces this in CI. See
[ADR-0005](markdown/adr/ADR-0005-licensing.md).

*This file summarises the licences; it is not legal advice. Where it and the
[LICENSE](LICENSE) text differ, the licence text governs.*
