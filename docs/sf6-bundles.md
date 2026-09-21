# SF6 bundles and menus

Fluffy Mod Manager can show several versions of a mod as one entry: a heading you
open, with the variants inside it. This page explains how to make that with
**File → Export → RE Mesh Editor → SF6 Fluffy Mod Folder**.

The examples use one character with two hairstyles, **Braid** and **Bangs**.

> **Interactive planner:** [SF6 Bundle Planner](https://zztaii.github.io/RE-Mesh-Editor/sf6-bundle-planner.html)
> works out your export list and shows what to type in every field.
> [`docs/sf6-bundle-planner.html`](sf6-bundle-planner.html) is the same page — download it and
> open it in a browser if you would rather not use the link. It only reaches the
> network for fonts and works fine without them.

## Before the first export

1. Import the mesh you want to edit, so the collection carries its original
   filename. The exporter reads the character, costume and slot from it.
2. Open the exporter and set **Parent Directory** to your Fluffy `Games\SF6\Mods`
   folder. Every export in these recipes goes straight into it.
3. Fill in **Author** and **Version** once. The dialog remembers every field for
   the next export, which is what makes these recipes short.

The mesh you select decides where the mod installs. Naming a folder "C2" does not
turn a C1 mesh into costume 2. Slots are **00 Head, 01 Body, 02 Hair**, and costume
folders keep three digits: `001`, not `01`.

## Recipe A — one flat bundle

Two variants under one heading, in two exports:

```
Hair Collection
    Braid
    Bangs
```

**Export 1.** Select the Braid mesh. Keep **Export Content** on `Mesh Mod`.

| Field | Value |
| --- | --- |
| Mod Folder Name | `Ingrid C1 Braid` |
| Display Name | `Braid` |
| Bundle Name | `Ingrid C1 Hair Collection` |
| Parent Mod | *(blank)* |

**Export 2.** Select the Bangs mesh and reopen the exporter. Change two fields:
**Mod Folder Name** to `Ingrid C1 Bangs` and **Display Name** to `Bangs`. Leave
**Bundle Name** exactly as it was — that is what groups them.

That is the whole recipe. A third variant is the same again with a new folder name
and display name.

## Recipe B — menus inside menus

Use this when you want a pack that opens into groups:

```
Ingrid C1 Style Pack
    Ingrid C1 Hair Options
        Braid
        Bangs
```

Four exports. **Bundle Name stays blank throughout** — the links are made with
**Parent Mod** instead.

**Export 1 — the main menu.** Switch **Export Content** to `Menu Only`. No mesh is
exported. A menu has no mesh to read the category from, so type **Category**
yourself.

| Field | Value |
| --- | --- |
| Mod Folder Name | `00 Ingrid C1 Pack` |
| Display Name | `Ingrid C1 Style Pack` |
| Parent Mod | *(blank)* |

**Export 2 — the hair submenu.** Still `Menu Only`.

| Field | Value |
| --- | --- |
| Mod Folder Name | `01 Ingrid C1 Hair Options` |
| Display Name | `Ingrid C1 Hair Options` |
| Parent Mod | `Ingrid C1 Style Pack` |

**Export 3 — Braid.** Select the Braid mesh and switch back to `Mesh Mod`.

| Field | Value |
| --- | --- |
| Mod Folder Name | `11 Ingrid C1 Braid` |
| Display Name | `Braid` |
| Parent Mod | `Ingrid C1 Hair Options` |

**Export 4 — Bangs.** Select the Bangs mesh. Change **Mod Folder Name** to
`12 Ingrid C1 Bangs` and **Display Name** to `Bangs`. **Parent Mod** stays on
`Ingrid C1 Hair Options`.

For a second group, repeat exports 2 to 4 with a new submenu name, for example
`Ingrid C1 Collar Options` holding Open Collar and Closed Collar. Those use the
body mesh rather than the hair mesh.

All four folders sit **side by side** in your Mods directory. You never move one
folder inside another; the links live in the `modinfo.ini` files.

## The four names people mix up

| Field | What it is | Written as |
| --- | --- | --- |
| Mod Folder Name | The folder on your drive. A new variant needs a new one; reusing a name updates that export. | *(the folder)* |
| Display Name | What you read in Fluffy. | `name=` |
| Bundle Name | Groups separate mods under one heading. Must be identical on every variant. | `nameasbundle=` |
| Parent Mod | The menu this entry goes inside. Matches the parent's **Display Name**, never a folder path. | `addonfor=` |

A `Menu Only` export also writes `dummymod=True`, which is what makes Fluffy open
it instead of installing it.

## What gets written

A mesh variant in Recipe A produces:

```ini
name=Braid
version=v1
author=Your Name
category=!Characters > Ingrid
nameasbundle=Ingrid C1 Hair Collection
```

alongside the mesh at
`natives\stm\product\model\esf\esf032\001\02\esf032_001_02.mesh.230110883`.

Exporting into a folder that already exists updates these files and leaves
anything else in the folder alone.

## Other fields

- **Additional Categories** takes a semicolon-separated list, such as `Hair; Colours`.
  Each becomes its own category line on top of the character category.
- **Description** breaks lines with a literal `\n` — a backslash, not a slash.
- **Preview Image** copies a PNG or JPEG into the mod folder. Menus can have one too.
- **Create Parent Menu** is a shortcut for naming a Parent Mod that does not exist
  yet; it creates that menu in the same export. Following the recipes above you
  never need it, because each menu is made before anything points at it. An
  existing menu that already matches is left untouched.

## If the exporter refuses

| Message | What to do |
| --- | --- |
| An add-on option must have a different display name from its parent mod. | Your Display Name and Parent Mod are the same text. Rename one. |
| The parent folder already belongs to a different mod. | Create Parent Menu found a folder that is not a matching menu. Point **Parent Menu Folder** somewhere new. |
| This folder belongs to a mesh mod. Choose a separate folder for the dummy menu. | A menu cannot reuse a mesh mod's folder. Give it its own. |
| The selected collection has no recognizable SF6 mesh filename. | The collection was not imported from an `esfNNN_CCC_SS.mesh` file. Re-import the original and edit that. |
| Enter a folder name without leading/trailing spaces or a trailing dot. | Trim the folder name. It also cannot contain `\ / : * ? " < > \|`. |

Entries that do not appear in Fluffy are usually a **Parent Mod** that matches no
existing Display Name, or two entries sharing one Display Name. Both are checked
for you by the planner. If Fluffy was already open when you exported, refresh its
mod list.
