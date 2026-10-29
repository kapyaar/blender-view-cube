# View Cube Navigator

Licensed under the MIT License. See `LICENSE`.

An Onshape-style viewport cube for Blender. Hover a face, edge line, or corner to highlight the next view, then click to align the viewport with a short eased transition. Edges are clickable along their length, without midpoint handles. The cube appears below Blender's standard navigation gizmo. Toggle it in the View Cube sidebar tab.

The add-on changes only the active viewport orientation. Its viewport click handler passes clicks outside the cube through to Blender. It does not edit scene data or startup files.

## Install

In Blender, open **Edit > Preferences > Add-ons**, use **Install from Disk**, select the release ZIP, and enable **View Cube Navigator**. Save preferences if you want it enabled on every launch. Do not extract the ZIP first.

The package declares Blender 5.2 as its minimum version. It was tested on Blender 5.2.2; a later Blender release may change the Python or gizmo APIs.

## Uninstall

Disable and remove **View Cube Navigator** in Blender's Add-ons preferences.
