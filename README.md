![REMeshEditorTitle](https://github.com/NSACloud/RE-Mesh-Editor/assets/46909075/156d0b53-ff4f-43db-9a3d-9e0cbd71326e)

# RE Mesh Editor — Street Fighter 6 fork

A Blender addon for importing and exporting RE Engine `.mesh` and `.mdf2` (material) files natively, with no Noesis required.

This is a fork of [RE Mesh Editor](https://github.com/NSACloud/RE-Mesh-Editor) by **NSA Cloud**. It retains the base addon's tools and adds a Street Fighter 6 workflow: source-preserving mesh edits, export straight into a Fluffy Mod Manager folder, and import/export validation. The fork's testing focuses on SF6; it does not establish compatibility for every other RE Engine game.

### [Download the latest release](https://github.com/ZZtaii/RE-Mesh-Editor/releases/latest)

Current release: **V0.66-SF6.5** · [Change log](#change-log) · [SF6 workflows](#street-fighter-6-workflows)

> **V0.66-SF6.5 is the stable fork release.** Keep backups of your work and follow the source-preservation limits below.

> **Tested with Blender 4.5.3 LTS.** For newer versions, check [the upstream Blender performance report](https://projects.blender.org/blender/blender/issues/155858) and its current status.

<img width="1888" height="945" alt="RE Mesh Editor in Blender" src="https://github.com/user-attachments/assets/3a072b6e-fa98-43d1-9fb0-34c2942c97a1" />

---

## What this fork adds

### SF6 source preservation
Import an original SF6 mesh with its source data embedded in the project, then patch supported edits into that original file. Unedited round trips have passed byte-for-byte checks. The exporter retains unknown deformation data, original normals, tangents, bounds and untouched LODs; it does not regenerate them for a changed silhouette. Large geometry edits and automatic lower-LOD updates remain unvalidated.

### SF6 Fluffy Mod Folder export
Export a finished mesh directly as an installable Fluffy Mod Manager folder, with the correct native game path and a `modinfo.ini` written for you. It also builds grouped mods: bundles that collect variants under one heading, and nested menus for larger packs.

### Import and export validation
A mesh file that fails to parse no longer wipes your scene first. Source-preservation export now refuses unsupported edits with an explanation instead of quietly falling back to the ordinary exporter and producing a file that breaks in game.

---

## Requirements

* [Blender 4.3.2 or higher](https://www.blender.org/download/) — this fork's SF6 work is validated on **Blender 4.5.3 LTS**.

**Not required, but strongly recommended:**
* [RE Chain Editor](https://github.com/NSACloud/RE-Chain-Editor) — Blender addon for creating chain files, used to add physics to models.
* [RE Asset Library](https://github.com/NSACloud/RE-Asset-Library) — Blender addon for extracting and importing RE Engine files. Removes most of the need to look up file IDs by hand.

---

## Installation

1. Download the addon zip from the [latest release](https://github.com/ZZtaii/RE-Mesh-Editor/releases/latest).
2. In Blender, go to **Edit → Preferences → Add-ons**, click the arrow in the top right of the addon menu and choose **Install From Disk**.

   ![Install From Disk](https://github.com/user-attachments/assets/49dd95c1-9a20-49d8-af55-7160d54836df)

3. Select the downloaded zip and click **Install Addon**.
4. Tick the box next to **RE Mesh Editor** in the addon list.

If you are replacing an existing installation, save your work and restart Blender afterwards.

**Updating:** reinstall from the [latest fork release](https://github.com/ZZtaii/RE-Mesh-Editor/releases/latest), then restart Blender. V0.66-SF6.4 and newer provide an **Open Fork Releases** button for this manual workflow. V0.66-SF6.3 and older builds still show the upstream updater; use the fork's release page instead of that updater.

The addon's internal version still reads `0.66`, which is the upstream version this fork is based on. The `SF6.x` part of the release tag is the fork's own revision.

---

## Street Fighter 6 workflows

Both SF6 features target Street Fighter 6 character meshes (`.mesh.230110883`). Other games continue to use the base addon's import/export paths and have not been revalidated by the SF6 tests.

SF6 character files follow a fixed layout, and the addon reads it from the imported filename:

| Part | Slot | Example file |
|---|---|---|
| Head | `00` | `esf032_001_00.mesh.230110883` |
| Body | `01` | `esf032_001_01.mesh.230110883` |
| Hair | `02` | `esf032_001_02.mesh.230110883` |

The three digits before the slot are the costume number, and they are kept exactly as they are. Naming an export folder "C2" does not turn a C1 mesh into costume 2 — the mesh you selected decides the game path.

### Editing a mesh while preserving the original

1. **Import** the original mesh from **File → Import → RE Mesh Editor → RE Mesh**, with **SF6: Preserve Source + Shape Keys** enabled. Leave **Merge Mesh Groups** and **Merge Armature** disabled.
2. Make your edits.
3. **Export** from **File → Export → RE Mesh Editor → RE Mesh**, with **SF6: Preserve Source Data** enabled.

What this mode supports: moving vertices, shape key edits, supported face deletions and `C_Hip` stubs. Unimported LODs, normals and bounds keep their original data.

What it does not support: new topology, UV edits, added or removed UV layers, and material reassignment. These checks stop the export with an explanation before replacing the destination file.

A project saved before this feature existed has no source metadata attached. Re-import the original mesh to use this mode with it; ordinary export still works as before.

### Batch export and preservation settings

**RE Batch Exporter** starts with **SF6: Preserve Source Data OFF**. It remembers your choice per mesh collection after a successful export. The Fluffy folder exporter remembers its own choice, initially ON; changing either exporter cannot change the other's preservation setting. Direct mesh exports also leave the batch choice alone.

When upgrading, the new independent batch setting starts OFF even in existing projects. Older versions shared a saved value between exporters, so that value is deliberately ignored. Enable batch preservation explicitly for collections that need it.

Legacy **RE Toolbox** batch calls use this same independent batch choice when they omit the preservation option. Updating RE Mesh Editor is sufficient for this compatibility fix; RE Toolbox itself does not need a patch. Direct exports with an explicit option or a displayed file-browser setting keep that choice.

Use ordinary export (preservation OFF) for added or replaced mesh parts, including new geometry joined into an existing object. This rebuilds the whole exported mesh and does not retain the original SF6 deformation data. Preservation ON still validates the original source layout; it cannot selectively skip new parts.

### Exporting a Fluffy mod folder

Open **File → Export → RE Mesh Editor → SF6 Fluffy Mod Folder**.

Set **Parent Directory** to your Fluffy `Mods` folder, choose **Mesh Mod**, and fill in the folder name, display name, author, version and category. The exporter writes the mod folder, the mesh at its native game path, and `modinfo.ini`, with an optional preview image. Your fields are remembered for the next export, so a second variant is mostly a matter of changing two names.

Three names are easy to mix up:

| Field | What it is | Example |
|---|---|---|
| Mod Folder Name | The folder on your drive | `11 Ingrid C1 Braid` |
| Display Name | The name shown in Fluffy | `Braid` |
| Parent Mod | The menu this entry sits inside | `Ingrid C1 Hair Options` |

**Parent Mod matches a Display Name, not a folder path.**

Other fields: **Additional Categories** takes semicolon-separated entries such as `Hair; Colours`, and **Description** accepts `\n` for a line break, following [Fluffy's format](https://www.patreon.com/posts/61589372).

Exports are staged before the destination is updated. Unrelated files and INI entries in an existing mod folder are left alone. On a commit failure, the exporter attempts to roll back both the mesh and its parent menu; if recovery itself fails, it reports the retained backup location.

### Grouping variants

**A bundle** puts several variants under one heading. Export each variant to its own folder with its own display name, and give every one of them the **same Bundle Name**. Leave Parent Mod empty.

```text
Ingrid C1 Hair Collection
    Braid
    Bangs
```

**Nested menus** build a deeper structure. Leave Bundle Name empty and use **Parent Mod** instead. Export a **Menu Only** entry for each menu level, then export each mesh with its Parent Mod set to the display name of the menu it belongs in.

```text
Ingrid C1 Style Pack
    Ingrid C1 Hair Options
        Braid
        Bangs
```

All the folders sit side by side in your Mods directory — you never nest them by hand, the exporter writes the links. If you name a Parent Mod that does not exist yet, tick **Create Parent Menu** and it will be created in the same export; an existing parent menu is reused, not overwritten.

> **On Windows,** avoid very deep export directories and very long mod folder names. Staged exports can run into the Windows path length limit.

For worked examples of both recipes, field by field, see the [SF6 bundle guide](docs/sf6-bundles.md). There is also an interactive [bundle planner](https://zztaii.github.io/RE-Mesh-Editor/sf6-bundle-planner.html) that lays out the menu structure you want and tells you what to type into each export.

**Scope:** folder export writes mesh payloads and menu metadata. It does not generate textures, MDF materials or chain files — those still go into the mod folder the usual way.

---

## Base addon features

The fork retains these tools from the original addon:

 - Importing and exporting of RE Engine mesh files.
 - Importing and exporting of RE Engine mdf2 (material) files.
 - MDF material editing from within Blender.

   <details>
   <summary>Video Preview</summary>

   https://github.com/user-attachments/assets/48be61bc-7c40-440f-881b-534809d3232f

   </details>

 - Preset system that allows for presets of materials to be saved and shared.
 - Supports LOD (level of detail) import and export.
 - Texture conversion tools.
 - Collection based system that allows for export with multiple mesh files in a scene.
 - Drag and drop texture conversion. (Blender 4.1 and higher)

   <details>
   <summary>Video Preview</summary>

   https://github.com/user-attachments/assets/da7fe4a6-b408-4269-ab33-3a52b1a71c54

   </details>

 - Drag and drop mesh importing. (Blender 4.1 and higher)

   <details>
   <summary>Video Preview</summary>

   https://github.com/user-attachments/assets/fa1ba74e-8a57-4115-b6cd-9585a2a92a21

   </details>

 - Batch exporting for all supported RE Engine file types.
 - Unpacking and repacking of mod pak files. (RE Asset Library required)
 - Automatic conversion from Blender material shaders to RE Engine.
 - Mod workspace system and file tracking for mod development. (RE Asset Library required)
 - Additional supported file types: **.fbxskel** (skeleton) and **.sfur** (shell fur).

### Supported games

Devil May Cry 5 · Resident Evil 2/3 Remake (RT and Non-RT) · Resident Evil 4 Remake · Resident Evil 7 Ray Tracing Version · Resident Evil 8 · Resident Evil 9 · Resident Evil Re:Verse · Monster Hunter Rise · Monster Hunter Wilds · Monster Hunter Stories 3 · **Street Fighter 6** · Dragon's Dogma 2 · Kunitsu-Gami: Path of the Goddess · Dead Rising Deluxe Remaster · Onimusha 2: Samurai's Destiny · Pragmata

This is the base addon's supported-game list. The source-preservation and folder-export features described above are specific to Street Fighter 6.

---

## Usage guide

[Written Model Importing Guide (WIP)](https://github.com/Modding-Haven/REEngine-Modding-Documentation/wiki/Custom-Model-Importing-Guide) — a full written walkthrough of importing a custom model into an RE Engine game. Still a work in progress.

<details>
  <summary>Short version: replacing a model</summary>

This is the ordinary mesh-rebuild workflow. For a new SF6 model or topology, disable **SF6: Preserve Source Data** at export. Ordinary export does not provide the source-mode deformation-data guarantee.

1. Find the mesh you want to replace inside the extracted .pak files.
2. Create a folder for your mod, then recreate the folder structure leading to the mesh file inside your mod folder, starting from the "natives" folder.
3. Import the mesh file from File > Import > RE Mesh. Use the default import settings.
4. Import the model you want to replace it with.
5. Pose your model and rig it to the armature from the imported mesh file.
6. In Edit Mode, select the geometry and separate by material (P > By Material) so that every mesh only has one material.
7. Move your meshes into the red .mesh collection, either by dragging them onto it in the outliner or pressing M (Move To Collection).
8. Rename your meshes to the same naming format as the imported mesh. (Example: Group_0_Sub_0__**MaterialName**)
9. Import the .mdf2 file that was alongside the .mesh file.
10. Rename the mdf material objects to your new material names in Object Properties > RE MDF Material Settings > Material Name.
11. Change the texture bindings in the RE MDF Material settings to new paths for any textures you want to change.
12. Duplicate or add material presets if necessary. **NOTE: The names and amount of materials in the mdf must match the mesh file or you will get either an invisible model or a checkerboard texture in game.**
13. Open your textures in Photoshop or any image editor with full .dds saving support. (Not Gimp) Install the [Intel DDS Plugin](https://www.intel.com/content/www/us/en/developer/articles/tool/intel-texture-works-plugin.html) if using Photoshop.
14. Adjust the color channels of your textures so that they match the corresponding RE Engine texture's color channel layout.
15. Save edited textures to their own folder as a .dds file. For the compression settings, use BC7 sRGB if it's an albedo/color map or BC7 Linear if it's anything else.
16. In the RE MDF tab on the sidebar, set the Mod Directory to the natives\STM\ folder inside your mod folder.
17. Set the image directory to the directory containing the .dds files you saved.
18. Press "Convert Directory to Tex", then press "Copy Converted Tex Files". The tex files will be placed at whatever path you set them to be in the MDF material.
19. To test the textures, press "Apply Active MDF". Check the console (Window > Toggle System Console) and make sure that there's no warnings about the textures that you created.
20. (Optional) Add bones to the armature to be used as physics bones. Create chains using RE Chain Editor.
21. Export from File > Export > RE Mesh/MDF and put them in the mod folder at their original chunk path.
22. Install the mod folder using Fluffy Manager or use FirstNatives.

For Street Fighter 6, **SF6 Fluffy Mod Folder** can handle the mesh portion of step 21 and write `modinfo.ini`. Export any MDF, texture and chain assets separately, then enable the mod in Fluffy as in step 22.

</details>

---

## FAQ / Troubleshooting

**The model has a checkerboard texture or is invisible in game.**
The material names or amount of materials in the mesh and MDF file do not match.

**The game infinitely loads when the model is loaded.**
A texture is not at the path set in the MDF. Or you may be using an outdated .mdf2, .pfb, or .user file in your mod. Make sure that you extracted from the most recent patch pak file.

**The model is stuck in a T pose.**
The mesh is not rigged to the armature correctly. Check that the mesh moves along with the armature in pose mode, and that the armature is inside the mesh collection.

**The material looks bugged in game.**
You may be using an outdated .mdf2 file. Extract from the latest patch pak, as materials can change on game updates.

**My SF6 export says it needs an original mesh imported with Preserve Source + Shape Keys.**
The collection has no source metadata — either it was never imported in that mode, or it was saved before the feature existed. Re-import the original mesh with **SF6: Preserve Source + Shape Keys**, or turn **SF6: Preserve Source Data** off to use the ordinary exporter.

**My SF6 export was rejected for a UV or material change.**
Source preservation does not support UV edits, added or removed UV layers, or material reassignment. Use the ordinary exporter for that kind of change.

**My bundle or menu did not appear in Fluffy.**
Check that **Parent Mod** exactly matches the parent entry's **Display Name**, and refresh Fluffy's mod list if it was already running. A bundle groups by an identical **Bundle Name** across variants, with Parent Mod left empty.

**For additional help:**

[RE Engine Modding Wiki](https://github.com/Havens-Night/REEngine-Modding-Documentation) · [Monster Hunter Modding Discord](https://discord.gg/gJwMdhK) · [Haven's Night Discord](https://discord.gg/modding-haven-718224210270617702)

Questions about the SF6 features in this fork belong on [this repository's issue tracker](https://github.com/ZZtaii/RE-Mesh-Editor/issues), not on the original author's.

---

## Change log

### V0.66-SF6.5
* Fixed legacy RE Toolbox batch compatibility when the caller omits the SF6 preservation option.
* Defaulted batch preservation OFF and separated its saved per-collection choice from folder and direct exports. Folder preservation keeps its own remembered choice, initially ON.
* Kept explicit direct-export options and displayed file-browser settings authoritative.
* Added clearer source-identity errors for new/replacement objects and the first mesh failure to RE Mesh Editor's batch error report.
* Added regression coverage for replacement meshes, legacy calls, independent settings and save/reopen behavior.

### V0.66-SF6.4
* Exposed and forwarded the SF6 source-preservation option in batch and quick export, and clarified its preference labels.
* Improved mesh operator failure reporting and background-process handling, and stopped writing unused UV snapshots on new imports.
* Replaced the upstream automatic updater with **Open Fork Releases** for manual installation.
* Added an informational notice when a mod folder already contains another SF6 character or costume. It does not block intentional combined mods.
* Added the public bundle guide and interactive planner, and updated this README for the fork.

### V0.66-SF6.3
* Added **SF6 Fluffy Mod Folder** export: writes the native mesh path and `modinfo.ini`, with an optional preview image. Direct mesh export is unchanged.
* Added bundles (**Bundle Name**), nested menus (**Parent Mod**), **Menu Only** entries and optional automatic parent menu creation.
* Folder, metadata and grouping fields are remembered between exports.
* Multiple categories and description line breaks are supported. Existing unrelated mod files and INI entries are preserved, and staged updates roll back together on failure.
* Corrected the SF6 part labels to 00 Head, 01 Body, 02 Hair, keeping the original costume and slot numbering.

### V0.66-SF6.2
* An invalid or missing mesh file no longer clears the scene before import validation fails.
* SF6 preservation export now rejects missing source metadata instead of silently falling back to the ordinary exporter.
* Unsupported UV changes, added or removed UV layers, object-linked material overrides and face material assignments are detected before the export destination is replaced.

### V0.66-SF6.1
* First SF6 source preservation build. Preserves the original deformation data and corrective shapes through supported edits, keeping the original mesh layout and LODs.
* Supports vertex and shape edits, supported deletions and `C_Hip` stubs.
* Validates supported edits before replacing the export destination.

The change log for the original addon, up to V0.66, is in the [upstream README](https://github.com/NSACloud/RE-Mesh-Editor?tab=readme-ov-file#change-log).

---

## Credits

Original addon by **[NSA Cloud](https://github.com/NSACloud)**. All of the RE Engine mesh, material and texture work this fork builds on is theirs.

- [Ando](https://github.com/Andoryuuta) - Solving the compression format for MH Wilds textures.
- [AsteriskAmpersand](https://github.com/AsteriskAmpersand) - Mesh format research and tex conversion code
- [AlphaZomega](https://github.com/alphazolam/) - RE Mesh 010 Template and Noesis plugin
- [CG Cookie](https://github.com/CGCookie) - Addon updater module
- [matyalatte](https://github.com/matyalatte/Texconv-Custom-DLL) - DirectX Texconv DLL library
- [PittRBM](https://x.com/wDnrbm) - NRRT texture node setup
- Ridog - NRRT normal conversion code used as reference

## License

GPL, same as the original. See [LICENSE.GPL](LICENSE.GPL).
