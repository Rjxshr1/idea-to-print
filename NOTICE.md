# External tools and services

The MIT license covers this repository's original workflow instructions,
Python helpers, tests and parametric example. It does not relicense third-party
software, hosted services, model weights, uploaded images or generated assets.
No model weights, personal models, printer credentials, Codex system skills,
or computer-control plugin binaries are distributed here.

- [Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1) and its
  [public demo](https://huggingface.co/spaces/tencent/Hunyuan3D-2.1): third-party
  image-to-3D service/model. The helper is an original API client, not a copy of
  the inference engine. Consult upstream license and service terms for your use.
- [Blender](https://www.blender.org/about/license/): separately installed GPL
  software used for editing and rendering; no Blender executable is bundled.
- [Bambu Studio](https://github.com/bambulab/BambuStudio): separately installed
  slicer. No Bambu profiles or binaries are redistributed. The LAN telemetry
  helper is experimental and unofficial; dispatch uses the official application.
- [OpenSCAD](https://openscad.org/): optional, separately installed parametric
  geometry engine. The included ribbon example is original project code.
- Python dependencies retain their own licenses. `requirements.txt` installs
  them from their providers instead of vendoring their source.
- Codex/ImageGen/computer-use capabilities must be provided by the user's host.
  Installing these skills does not grant paid model access or bundle those tools.

Users must have the necessary rights to images they submit and outputs they
publish. Job directories are excluded from version control by default. Sending
an image to the hosted shape service transfers that file to the named provider.
