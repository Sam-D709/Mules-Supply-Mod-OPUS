# Mules-Supply-Mod-OPUS

**Current test package: v0.1.0-beta.9 / internal 671, independent installation identity hotfix.** Trading rules remain those of beta.7; M1-M3 runtime files are unchanged. See [release notes](docs/releases/v0.1.0-beta.9.md) and [logging guide](docs/M4_DEBUG.zh-CN.md).

**Historical beta.7 test build, internal version 669.** [Build notes](docs/releases/v0.1.0-beta.7.md). Adds flexible capacity bands, fixes return-poll interrupt priority, repairs shrinking drafts within a budget, relaxes loading by game wait time and shares coarse supplier ordering. See [save10→11 findings and fixes](docs/BETA6_FIXES.zh-CN.md) and the [Debug guide](docs/SHIP_LOG.zh-CN.md). Native pressure testing identified remaining wait-age and logging issues; beta.9 retains those limitations while isolating the installation identity.

[简体中文](README.md) | **English**

A supply-focused mod for **X4: Foundations**, based on **Mules, Supply and Warehouses Extended 6.60**. It mainly rewrites **M4 — Supply Mule**: let idle ships plan independently using actual deliverable volume and estimated trip time, combine procurement lots, and complete small remaining demands. M1, M2 and M3 remain available.

Existing gameplay feedback is from **X4 8.00 Hotfix 4 (legacy) with Star Wars Interworlds**. Unmodded 8.00 and 9.00 have not been gameplay-tested. The latest player-supplier preference and localization changes still need native-game verification.

## Download and install

Download the precompiled **v0.1.0-beta.9 / internal 671** package from [Releases](https://github.com/Sam-D709/Mules-Supply-Mod-OPUS/releases/tag/v0.1.0-beta.9), or build `dist/Mules-Supply-Mod-OPUS-v0.1.0-beta.9.zip` using `python tools/build.py`.

1. Exit X4 and back up your saves. When upgrading, move the old `mules_supply_mod_opus` folder outside `extensions` before installing the replacement.
2. Extract the downloaded ZIP. It contains one folder: **`mules_supply_mod_opus`**.
3. Drag that entire folder into your X4 **`extensions`** directory. No build tools, source compilation or language configuration are required.
4. Launch X4 and confirm **Mules-Supply-Mod-OPUS** is enabled in Extensions. Configure M4 on your ships.

The result must look like this:

```text
X4 Foundations/
└── extensions/
    └── mules_supply_mod_opus/
        ├── content.xml
        ├── ext_01.cat
        ├── ext_01.dat
        ├── README.md
        └── README.en.md
```

For example: `E:\SteamLibrary\steamapps\common\X4 Foundations\extensions\mules_supply_mod_opus\content.xml`.

This build uses the independent local extension ID `mules_supply_mod_opus` and the matching folder. When upgrading from beta.8 or earlier, move the old `extensions/mules_and_warehouses_extended` folder outside `extensions` first. Install into the new folder; never copy this build into the Workshop original's folder. Unsubscribe from or disable the original Mules mod: both versions still define the same scripts and ship behaviors and must not be enabled together. Back up existing saves and check that this extension is enabled after migration; reselect the M4 behavior if necessary. Do not nest the extracted folder inside another mod folder. The ZIP includes Chinese and English resources; X4 selects them using its game language. The explicit English `l044` resource is generated from the default English text during packaging.

Existing settings may be preserved: change old ships from one procurement lot to three if desired. The retired Player Buy Mod percentage is not converted into the new stock threshold, which defaults to 50%.

## Dependencies and optional mods

Core procurement and compact ship logs have **no mandatory third-party mod dependency**. No additional DLC requirement is declared.

| Mod | Requirement | Purpose |
|---|---|---|
| [SirNukes Mod Support APIs](https://steamcommunity.com/sharedfiles/filedetails/?id=2042901274) | **Optional; required for the Debug menu** | Supplies Extension Options. Without it, the menu is unavailable and detailed Debug defaults to off; core procurement is independent. |
| kuertee UI Extensions and HUD | Not required | The menu calls SirNukes APIs directly, with no direct UIX API dependency. |
| Civilian Fleets | Optional compatibility | Detected to avoid duplicate subordinate behavior synchronization. |
| Subordinate Order Access | Optional convenience | Helps manage subordinate orders; not a runtime dependency. |

SWI is the current test environment, not a dependency. ship_sorter, TacticalResponsePolice and Options Helper are not required. The original Mules mod is included/replaced, not a separate prerequisite. The `-scriptlogfiles` startup flag is only needed for detailed disk logging.

## M4 settings compared with the original

Defaults below apply to newly configured behaviors.

| Original 6.60 setting | This mod | Reason |
|---|---|---|
| Max Trades: fixed at 1 | **Maximum Procurement Lots Per Trip**: 1–10, default 3 | Combine pickups without letting one ship monopolize every tiny demand. One supplier, one ware and one receiver count as a lot; delivery is not counted again. |
| Fill Cargo Hold %: 25–95%, default 75% | **Minimum Total Trip Load %**: 0–95%, default 75% | Apply the threshold to the combined trip. A secondary lot may be small; three completed insufficient-load failures allow a relaxed next search. |
| Collect from Player | **Prefer Player-Owned Suppliers**: enabled by default | Prefer eligible owned supply when on. When off, owned supply remains eligible and competes with NPC supply normally. |
| Player Buy Mod: 0–100% price multiplier | **Player Supplier Stock Threshold %**: 0–100%, default 50% | Keep low-stock sales factories out of internal supply while letting stocked factories support logistics. This is an eligibility trigger, not a retained reserve. |
| % of trades to eval | Removed from the visible menu | Shared discovery and batched processing handle search work; players do not need to tune a candidate percentage. |
| Allow Build Storage | **Supply Construction Storage**: enabled by default | Deliver construction materials to the receiver's build storage; this does not collect materials from build storage. |
| Allow Resources | **Supply Externally Produced Consumables**: enabled by default | Supply wares the receiver consumes but does not produce, such as microchips for an advanced electronics factory. Not limited to ore or gas. |
| Allow Intermediates | **Supply Self-Produced Consumables**: enabled by default | Supply wares the receiver both produces and consumes when internal production cannot keep up. |
| Allow Trade Wares | Removed from the visible menu | Supply trade inventory from valid buy offers. The inherited special filter for final products still applies. |
| Serve Source Only | **Supply Home Station/Sector Only**: disabled by default | Restrict recipients to the home station and its build storage, or player stations in the home sector. Suppliers may still be elsewhere within range. |
| No equivalent | **Secondary corridor radius (jumps)**: 0–10, default 2 | Limit secondary suppliers to sectors near the fixed home-to-primary-supplier route, also respecting the home range. |
| No equivalent | **Price markup per off-route jump %**: 0–50%, default 5% | Cost tie-break between otherwise equal trip rankings; throughput takes precedence. No change to payment. |
| Home Station/Sector; Assign Ship To Station; Max Jumps From Home | Retained | Set the service anchor and range. Station assignment requires a specific eligible player station. |
| Collect from AI Factions; Lock Ware List; Wares | Retained | Independently control NPC sources and which wares may be procured. |

With a player-sector home and home-only service enabled, M4 supplies player stations in that sector, not NPC recipients. NPC stations can still be suppliers. An explicitly selected NPC station home follows the inherited NPC service rules.

## Planning and supplier selection

Every idle M4 plans its own trip using a shared home demand/supplier index. Cargo capacity affects feasibility and load percentage; ship capacity no longer determines a fleet-wide turn order. A ship takes the quantities it successfully commits.

Use net sell-offer availability, not physical factory inventory. Clamp against remaining demand, supplier availability, native reservations, mod holds not yet visible natively, cargo space, budgets and minimum lot sizes. Volume is quantity multiplied by the native ware volume.

For each ware, shortlist high-volume, nearby and low-price suppliers, plus owned and rotating candidates. Validate actual ship eligibility and prices only for this shortlist. Failed searches widen and rotate it. Compare alternative primary pickups and bounded combinations, instead of fixing every trip to the nearest tiny source.

Rank trips by minimum-load feasibility, then owned volume as a fraction of cargo capacity when preference is enabled, then **deliverable cubic metres / estimated trip time**. Actual cost with corridor markup breaks otherwise equal rankings. Time includes travel from the current ship sector through pickups to delivery. S/M use relative weights of 90 per gate and 45 per loading/unloading stop; L/XL use 180 and 120. These are heuristics, not native ETA: local flight, congestion, speed and real dock queues are not modeled.

| Situation | Expected choice |
|---|---|
| Nearest source has a tiny amount; a farther one can fill the ship | Compare alternative anchors; the tiny source does not automatically win |
| Nearby source fills 80%; distant source fills 100%; minimum 75% | Both are feasible; estimated trip throughput decides |
| Two compatible wares each fill 40% | Combine them if receiver, storage and corridor rules allow |
| One ware is spread across factories | Separate pickups are separate lots under the configured limit |

Trips share one receiver and compatible storage type. Scope and ware-use priority tiers remain; secondary lots can cross allowed ware-use categories. Each alternative primary defines its own fixed home-to-primary corridor. Secondary pickups respect both corridor and home limits.

Search is bounded at 192 candidate pair checks and 128 route extensions per attempt. Each state considers up to six extensions and retains three states. Failed/exhausted searches rotate suppliers and demand windows. This is not a global optimal-route solver. Maximum purchases is a limit, not a required count.

After three consecutive completed searches fail for insufficient combined load, the fourth temporarily waives minimum load. Other trade failures interrupt this count; exhausted search budgets do not increment it. Success or configuration changes reset it. All other eligibility, funds, quantity and route rules still apply.

Eligible owned suppliers must strictly exceed their per-ware allocated-storage stock threshold, enabled or disabled preference alike. The default is 50%; it is an entry trigger, not a retained reserve after loading. With preference off, owned sources still participate normally. The NPC switch remains independent.

Shared discovery filters unrelated NPC demand before per-ship planning. Newly discovered quotes are inserted incrementally, with a 30-game-second fallback refresh; events during a snapshot trigger another refresh. A global token protects only final net checks and order creation, with no yields inside. Commit reads live prices and proceeds if they still satisfy affordability and spread rules, rather than rejecting any price change.

## Cancellations, diagnostics and idle ships

- Canceling a trade releases its demand for later reassignment without counting native reservations twice.
- Changing the home sector moves the ship to the new home's dispatch group and search area. An idle ship with no suitable trade returns to the new home. Already queued trades remain intact.
- Check an individual ship's **Information → Behavior** failure message for rejection reasons. This is a behavior diagnostic, not financial transaction history. It reports the last actual rejection, not an exhaustive explanation of all suppliers.
- **Information → Logbook** leads with the final result and specific failure, followed by combined load, actual secondary-search attempts and selected purchases. Failures of the same class, configuration and home have a 10-game-minute cooldown; a different class is reported immediately, and success is always recorded. Counts use existing checks and describe attempts, not unique suppliers. Native ship log entries also appear in the general logbook.
- For detailed diagnostics, start X4 with **`-scriptlogfiles`**, then enable **Extension Options → Mules-Supply-Mod-OPUS → Debug: detailed disk logging**. The menu uses the public SirNukes Mod Support APIs interface alongside kuertee UI Extensions; Mod Support APIs is needed for the menu, while normal procurement works without it. The switch is off by default and saved per savegame. Detailed candidates go only to **Documents/Egosoft/X4/[user ID]/logs/MulesExtended/debug.txt**. X4 scripts cannot write directly into the mod installation folder. Debug can reduce performance; disable it after reproducing the issue. Upgrading preserves old log entries and queued trades, and removes only obsolete full-report caches.
- Each idle M4 with no active or queued orders returns near its home station or into its home sector, unless the player is flying it. Other busy M4 ships do not delay its return. Returning ships do not need to occupy a trade berth.
- Return flight continues across 30-second dispatch checks; a check with no new job does not restart the route or travel-drive charging. Disabling home-only service still allows other player receivers within the current home's range.
- When a ship has no active or queued orders, leftover cargo is virtually liquidated: cargo is deleted and its average-price value is credited to the player. This is not a station delivery and can include manually loaded cargo.
- M4 follows valid buy offers. It does not rewrite automatic storage allocation, automatic prices or research demand targets.

## Verification and source

Actual XML adapter regressions cover supplier quantities, route tradeoffs, mixed categories, three failed load attempts, interleaved planning, commit collisions, dynamic discovery and bounded search. Package tests extract and verify runtime bytes. See the [test record](docs/TESTING.zh-CN.md).

These checks cannot establish native save migration, flight, event timing or FPS. Beta.7 needs 30 minutes with SETA and one hour without SETA in the native game. Existing feedback from older builds is not validation of this build.


```powershell
python tests/checks.py
python tests/test_package.py
python tools/build.py
```

The local build generates `dist/Mules-Supply-Mod-OPUS-v0.1.0-beta.7.zip`; this is a local test package. Advanced implementation notes and the testing matrix are currently in Chinese: [design](docs/DESIGN.zh-CN.md), [testing](docs/TESTING.zh-CN.md), [changes](docs/CHANGELOG.zh-CN.md).

Derived from [Mules and Warehouses Extended](https://github.com/Misunderstood-Wookiee/Mules-and-Warehouses-Extended), retaining **CC BY 4.0** and upstream attribution. See [LICENCE.md](LICENCE.md) and [ATTRIBUTION.md](ATTRIBUTION.md). This is an independently maintained derivative, not an official upstream or Egosoft release.

## Current feedback fixes

Candidate discovery and route expansion have independent work limits. Existing candidates still form multi-lot routes after discovery reaches its limit. Net supply and demand refresh before ranking and during each bounded extension; commit-time checks still guard later changes. Compact ship logs lead with the final failure, count actual secondary extensions and avoid duplicate idle reports. Debug records game time, ship and attempt so interleaved searches can be reconstructed; every attempt result is written even during the player-log cooldown.

Beta.7 prefers main lots suited to real cargo capacity bands; secondary lots can cross bands. Cross-band main fallback starts after 90 game seconds or three unsuccessful attempts. Loading caps drop to 50% after two game minutes, 25% after five, and zero after ten; three completed load failures can waive the minimum earlier. Quantity repair adds at most two lots from at most twelve existing candidates and still passes final validation. Return polling uses priority 1, below attacked response priority 10. Debug BEGIN identifies `build=beta.7`; native pressure testing is pending.
