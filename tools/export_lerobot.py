"""Convert a dataset recorded by the GUI's Recorder into a LeRobotDataset (v3).

Run it in an environment that has LeRobot with its dataset extra installed:

    pip install "lerobot[dataset]"
    python tools/export_lerobot.py recordings/block-box-v1 --repo-id local/block-box-v1
    python tools/export_lerobot.py recordings/block-box-v1 --repo-id me/block-box-v1 --push

The GUI itself does not need LeRobot; this script uses LeRobot's own writer so the output
format is whatever your installed LeRobot version expects. Episodes marked "short" are
skipped (use --include-short to keep them).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def read_frames(video_path: Path):
    """Yield RGB uint8 frames from an episode video."""
    import cv2

    capture = cv2.VideoCapture(str(video_path))
    try:
        while True:
            ok, bgr = capture.read()
            if not ok:
                return
            yield cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    finally:
        capture.release()


def export(source: Path, repo_id: str, root: Path | None, push: bool, include_short: bool) -> int:
    import numpy as np
    from lerobot.datasets import LeRobotDataset

    meta = json.loads((source / "dataset.json").read_text(encoding="utf-8"))
    joints = meta["joint_names"]
    camera = meta.get("camera_key")
    episodes = sorted(p for p in source.iterdir() if p.is_dir() and (p / "meta.json").exists())
    if not episodes:
        print("No episodes found.", file=sys.stderr)
        return 1

    features = {
        "observation.state": {"dtype": "float32", "shape": (len(joints),), "names": joints},
        "action": {"dtype": "float32", "shape": (len(joints),), "names": joints},
    }
    first_video = next((p / f"{camera}.mp4" for p in episodes if camera and (p / f"{camera}.mp4").exists()), None)
    if first_video is not None:
        first = next(read_frames(first_video))
        height, width = first.shape[:2]
        features[f"observation.images.{camera}"] = {
            "dtype": "video", "shape": (height, width, 3), "names": ["height", "width", "channels"]}

    dataset = LeRobotDataset.create(
        repo_id=repo_id, fps=int(meta["fps"]), features=features, root=root,
        robot_type=meta.get("robot_type") or None, use_videos=first_video is not None)

    written = 0
    for folder in episodes:
        info = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if info.get("status") == "short" and not include_short:
            print(f"skip {folder.name}: too short")
            continue
        data = np.load(folder / "data.npz")
        state, action = data["state"], data["action"]
        frames = list(read_frames(folder / f"{camera}.mp4")) if first_video is not None else None
        count = len(state) if frames is None else min(len(state), len(frames))
        if frames is not None and len(frames) != len(state):
            print(f"{folder.name}: {len(state)} samples but {len(frames)} video frames; using {count}")
        for i in range(count):
            frame = {"observation.state": state[i], "action": action[i], "task": info.get("task") or "task"}
            if frames is not None:
                frame[f"observation.images.{camera}"] = frames[i]
            dataset.add_frame(frame)
        dataset.save_episode()
        written += 1
        print(f"wrote {folder.name}: {count} frames")
    dataset.finalize()   # without this the parquet files are incomplete
    if push:
        dataset.push_to_hub()
    print(f"Done: {written} episode(s) -> {dataset.root}")
    return 0 if written else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="dataset folder written by the Recorder")
    parser.add_argument("--repo-id", required=True, help="e.g. local/my-dataset or user/my-dataset")
    parser.add_argument("--root", type=Path, default=None, help="where to write the LeRobot dataset")
    parser.add_argument("--push", action="store_true", help="push to the Hugging Face Hub afterwards")
    parser.add_argument("--include-short", action="store_true")
    args = parser.parse_args()
    if not os.path.exists(args.source / "dataset.json"):
        print(f"{args.source} is not a Recorder dataset (no dataset.json).", file=sys.stderr)
        return 1
    return export(args.source, args.repo_id, args.root, args.push, args.include_short)


if __name__ == "__main__":
    sys.exit(main())
