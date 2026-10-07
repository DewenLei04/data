# env/fonts.md -- font coverage record

Four families are required by the design row's default-runnable-software list. Ubuntu 22.04
does not ship the genuine Microsoft families; this file records what the runtime sandbox
installs instead, why that is sufficient in-container, and where the genuine families
actually end up in the delivered artifacts.

## Metric-compatibility table

| Required family | Metric-compatible provider (apt package) | Concrete face name |
|---|---|---|
| Times New Roman | `fonts-liberation2` (fallback: `fonts-liberation`) | Liberation Serif |
| Arial | `fonts-liberation2` (fallback: `fonts-liberation`) | Liberation Sans |
| Courier New | `fonts-liberation2` (fallback: `fonts-liberation`) | Liberation Mono |
| Calibri | `fonts-crosextra-carlito` | Carlito |

Both `env/packages-apt.txt` LAYER 4 entries above are installed by `env/Dockerfile`'s font
layer, which also runs `fc-cache -f` and asserts (via `fc-match`) that each of the four family
names resolves to a Liberation or Carlito face before the build is allowed to continue.

## 1. Why metric compatibility, not glyph identity, is what matters in-container

The property this case depends on is pagination stability, not visual font identity. Liberation
Serif/Sans/Mono and Carlito are deliberately built to have the *same character widths and line
metrics* as Times New Roman/Arial/Courier New/Calibri respectively. That means a `.docx` opened
by LibreOffice inside the sandbox reflows to the identical number of pages and the identical
line breaks as it would under the genuine Microsoft fonts, even though the rendered glyphs look
slightly different. Since one of this case's load-bearing traps is an answer anchor placed
beyond page 300, an in-container substitution that changed pagination would silently move the
anchor and break the case. Metric compatibility is the guarantee that prevents that; glyph
identity is cosmetic and is not required for anything the agent or the scorer inspects
in-container.

## 2. The handoff split -- where the genuine families actually land

Authored `.docx` deliverables declare the genuine Microsoft family names directly in their
`w:rFonts` XML (Times New Roman / Arial / Courier New / Calibri). The runtime sandbox never
converts those documents to the final delivered PDF/PNG -- that conversion is performed by the
*user*, on Windows, where the real fonts are installed. So the artifacts that actually ship
embed the genuine families; the sandbox's Liberation/Carlito substitution only ever affects how
the agent under test *views* documents while working inside the container.

## 3. Recorded departure from the stack sketch -- no `ttf-mscorefonts-installer`

The stack research for this case sketches installing
`ttf-mscorefonts-installer` inside the image so the container itself has the genuine fonts. This
plan deliberately does NOT install `ttf-mscorefonts-installer` (or any other Microsoft-licensed
font installer package). That package fetches font archives from a third-party mirror at apt
*install* time, requires an EULA acceptance step via `debconf-set-selections`, and the mirror
has a documented flakiness history -- none of which is compatible with an offline, statically
verifiable, unattended image build. The consequence is that a PDF produced *inside* the
container (if the agent renders one) embeds Liberation/Carlito faces rather than the genuine
Microsoft families. This is accepted and expected: it does not affect pagination (see section 1)
and it does not affect the delivered artifacts (see section 2), which are produced on the
Windows side where the genuine fonts already exist.

Page-count parity between the container's in-sandbox render and the Windows-side conversion is
verified downstream by the corpus QC gates, not by this file or by the manifest gate.
