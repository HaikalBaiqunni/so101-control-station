# SO-101 model

`scene.xml`, `so101_new_calib.xml` and `assets/*.stl` are copied unchanged from
[TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100)
(`Simulation/SO101/`), which is licensed under the Apache License 2.0
(see `LICENSE-Apache-2.0.txt`). Only the meshes `so101_new_calib.xml` references
were copied. The MJCF was generated from the Onshape CAD model with
[onshape-to-robot](https://github.com/Rhoban/onshape-to-robot); motor parameters
come from the Open Duck Mini project (see the upstream `Simulation/SO101/README.md`).

"New calibration" variant: each joint's zero is the middle of its range, which
matches how this app maps a calibrated joint onto the twin.
