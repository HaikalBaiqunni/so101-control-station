"""Builds docs/MANUAL.html (English + Japanese in one page, screenshots embedded).

    python tools/make_manual.py

The screenshots are taken from the real GUI by tools/manual_shots.py, so re-running
this after a UI change refreshes the manual. Text lives here, in EN/JA pairs.
"""
from __future__ import annotations

import base64
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

CSS = (ROOT / "tools" / "manual.css").read_text(encoding="utf-8")
VERSION = "0.2.0"
REPO = "https://github.com/HaikalBaiqunni/so101-control-station"
IMAGES: dict[str, str] = {}   # name -> data URI, filled by main()


# ------------------------------------------------------------------ tiny HTML helpers
def sp(en: str, ja: str) -> str:
    return f'<span data-i="en">{en}</span><span data-i="ja">{ja}</span>'


def p(en: str, ja: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f'<p{c} data-i="en">{en}</p>\n<p{c} data-i="ja">{ja}</p>\n'


def h3(en: str, ja: str) -> str:
    return f"<h3>{sp(en, ja)}</h3>\n"


def ul(items: list[tuple[str, str]]) -> str:
    out = '<ul class="plain">\n'
    for en, ja in items:
        out += f'  <li data-i="en">{en}</li>\n  <li data-i="ja">{ja}</li>\n'
    return out + "</ul>\n"


def callout(kind: str, icon: str, en: str, ja: str) -> str:
    return (f'<div class="callout {kind}"><div class="ico">{icon}</div>'
            f'<p data-i="en">{en}</p><p data-i="ja">{ja}</p></div>\n')


def fig(name: str, alt: str, cap_en: str, cap_ja: str, cls: str = "wide") -> str:
    return (f'<figure class="shot {cls}"><img src="{IMAGES[name]}" alt="{alt}" loading="lazy">\n'
            f'  <figcaption data-i="en">{cap_en}</figcaption>\n'
            f'  <figcaption data-i="ja">{cap_ja}</figcaption></figure>\n')


def table(head: tuple[tuple[str, ...], tuple[str, ...]], rows: list[tuple[tuple[str, ...], tuple[str, ...]]],
          mono_first: bool = False) -> str:
    """head = (en cells, ja cells); rows = [(en cells, ja cells), ...]"""
    def cells(tag, items):
        return "".join(f"<{tag}>{c}</{tag}>" for c in items)

    def body(lang_index):
        out = ""
        for row in rows:
            items = row[lang_index]
            first = f'<td class="mono">{items[0]}</td>' if mono_first else f"<td>{items[0]}</td>"
            out += "<tr>" + first + "".join(f"<td>{c}</td>" for c in items[1:]) + "</tr>\n"
        return out

    return (
        '<div class="tablewrap"><table>\n<thead>\n'
        f'<tr data-i="en">{cells("th", head[0])}</tr>\n<tr data-i="ja">{cells("th", head[1])}</tr>\n</thead>\n'
        f'<tbody data-i="en">\n{body(0)}</tbody>\n<tbody data-i="ja">\n{body(1)}</tbody>\n</table></div>\n'
    )


def step(n: int, title: tuple[str, str], body: str) -> str:
    return (f'<div class="step"><div class="step-num">{n}</div><div class="step-body">\n'
            f'{h3(*title)}{body}</div></div>\n')


def stage(anchor: str, num: str, title: tuple[str, str], why: str, body: str) -> str:
    badge = f'<span class="stage-num">{num}</span>' if num else ""
    return (f'<section id="{anchor}" class="stage">\n<div class="stage-head">{badge}'
            f'<h2>{sp(*title)}</h2></div>\n{why}{body}</section>\n')


def why(en: str, ja: str) -> str:
    return p(en, ja, "stage-why")


# ------------------------------------------------------------------ content
def content() -> str:
    parts: list[str] = []

    # ---- Before you start
    parts.append(stage("start", "", ("Before you start", "はじめに"), "", (
        p("This app drives two robots from one window: the <strong>SO-101 / SO-ARM100</strong> (Feetech STS3215 "
          "servos) and the <strong>reBot B601-DM</strong> (Damiao CAN motors, with Seeed's Star Arm 102 as its "
          "leader). It does not need the full LeRobot package (no PyTorch, no dataset stack). You need Python 3.10+ "
          "and the robot itself. For the SO-101 that is the arm, a USB serial adapter and — the single most common "
          "&quot;my arm is dead&quot; cause — the arm's own <strong>5&nbsp;V power supply</strong>: USB alone powers the "
          "adapter, not the servos. The B601-DM needs a USB-to-CAN adapter for the follower and, for teleoperation, "
          "the Star Arm 102 on its own USB-serial port.",
          "本アプリは1つのウィンドウで2種類のロボットを扱います：<strong>SO-101 / SO-ARM100</strong>（Feetech STS3215サーボ）と "
          "<strong>reBot B601-DM</strong>（Damiao CANモーター。リーダーはSeeedのStar Arm 102）。LeRobot本体（PyTorchや"
          "データセット関連）は不要です。必要なのはPython 3.10以降とロボット本体です。SO-101の場合は、アーム、USBシリアル"
          "アダプタ、そして「アームが全く反応しない」原因で最も多い、アーム本体用の<strong>5V電源</strong>です（USB給電では"
          "アダプタしか動かず、サーボは動きません）。B601-DM の場合はフォロワー用のUSB-CANアダプタと、遠隔操作用にStar Arm 102"
          "（専用のUSBシリアルポート）が必要です。") +
        '<div class="tablewrap"><table><tbody>'
        '<tr><td class="mono">git clone ' + REPO + '.git</td></tr>'
        '<tr><td class="mono">cd so101-control-station</td></tr>'
        '<tr><td class="mono">python -m venv venv &amp;&amp; venv\\Scripts\\activate</td></tr>'
        '<tr><td class="mono">pip install -r requirements.txt</td></tr>'
        '<tr><td class="mono">python main.py</td></tr></tbody></table></div>\n' +
        p("The app opens on the <strong>stage</strong>: a 3D digital twin with the controls floating over it. Both "
          "robots ship with a twin model, so it loads by itself. Pick the robot in the top-left corner; everything "
          "below says which robot a section is for.",
          "アプリは<strong>ステージ</strong>で起動します。3Dのデジタルツインの上に操作パネルが浮かぶ構成です。どちらのロボットにも"
          "ツインのモデルが同梱されているため、自動で読み込まれます。左上でロボットを選びます。以降の各節に、どちらのロボット向けかを"
          "明記しています。") +
        callout("warn", "ⓘ",
                "None of the screenshots were taken with hardware connected. In the ones that show &quot;Follower "
                "online&quot; or a leader ghost, the connection state and poses are simulated to show the layout.",
                "スクリーンショットはいずれも実機を接続せずに撮影しています。「Follower online」やリーダーのゴーストが表示されている"
                "画面は、レイアウトを示すために接続状態と姿勢を模擬したものです。"))))

    # ---- 01 the screen
    parts.append(stage("screen", "01", ("The screen", "画面の見方"),
        why("One window, no tabs. The digital twin fills the background; the tools sit on it as cards, and "
            "the heavier ones open as drawers or in the Setup hub.",
            "タブはありません。デジタルツインが背景全体に表示され、各ツールはカードとして重なります。大きなツールは"
            "ドロワーやSetupハブで開きます。"),
        fig("stage_so101", "The stage on the SO-101: top bar, View card, Jog card and the Connection and Control source dock",
            "The stage on the SO-101 (twin at a made-up pose). Top bar, View card (left), Jog card (right), dock with Connection and Control source (bottom).",
            "SO-101のステージ（ツインは適当な姿勢）。トップバー、Viewカード（左）、Jogカード（右）、ConnectionとControl sourceのドック（下）。") +
        step(1, ("The top bar", "トップバー"),
            fig("topbar", "Top bar with robot picker, status chips, search, Waypoints, Telemetry, Setup, About and Stop",
                "Left: robot picker and four status chips. Right: search, drawers, Setup, About and the red Stop.",
                "左：ロボット選択と4つのステータスチップ。右：検索、ドロワー、Setup、About、赤いStop。", "wide") +
            table((("Item", "What it does"), ("項目", "内容")),
                  [(("Robot", "Switches the whole app between SO-101 and reBot B601-DM. Any connection belonging to the other robot is closed."),
                    ("Robot", "アプリ全体をSO-101とreBot B601-DMで切り替えます。もう一方のロボットへの接続は切断されます。")),
                   (("Chips", "Follower online/offline, Leader online/offline, Torque ON/off, and the control source (Manual, Gamepad, Keyboard, Leader (standby), Teleop engaged)."),
                    ("チップ", "Follower online/offline、Leader online/offline、Torque ON/off、コントロールソース（Manual / Gamepad / Keyboard / Leader (standby) / Teleop engaged）。")),
                   (("Search (Ctrl+K)", "Command palette: type to jump anywhere or toggle a view setting."),
                    ("検索（Ctrl+K）", "コマンドパレット。入力して各画面へ移動したり、表示設定を切り替えます。")),
                   (("Waypoints, Telemetry, Tune", "Open a drawer on the right. One drawer at a time. Tune exists only for the B601-DM."),
                    ("Waypoints / Telemetry / Tune", "右側にドロワーを開きます（同時に開けるのは1つ）。TuneはB601-DM専用です。")),
                   (("Setup", "Opens the Setup hub: Motors and ids, Calibration, Inputs, Data and logs."),
                    ("Setup", "Setupハブを開きます：Motors and ids / Calibration / Inputs / Data and logs。")),
                   (("i (About)", "Creator, version, licence and the tech stack."),
                    ("i（About）", "作者、バージョン、ライセンス、技術スタック。")),
                   (("Stop", "Follower torque OFF and control back to Manual, at once. Also Ctrl+Shift+Space."),
                    ("Stop", "フォロワーのトルクをOFFにし、コントロールをManualに戻します（即時）。Ctrl+Shift+Spaceでも実行できます。"))])) +
        step(2, ("The View card", "Viewカード"),
            '<div class="shotgrid">' +
            fig("card_view", "View card with Ghost target, Axes, HUD overlay and Camera switches", "The View card.", "Viewカード。", "tight") +
            "<div>" + ul([
                ("<strong>Ghost target</strong> — a translucent copy of the arm at where it is heading (a jog target, the selected waypoint, or the leader's pose on standby).",
                 "<strong>Ghost target</strong> — アームが向かっている先（ジョグの目標、選択中のウェイポイント、待機中のリーダーの姿勢）を半透明で重ねます。"),
                ("<strong>Axes</strong> — draws the World axes at the base and the Tool axes at the gripper; the frame the jog acts in is drawn thick.",
                 "<strong>Axes</strong> — ベースのWorld軸とグリッパーのTool軸を描画します。ジョグが使う座標系は太く表示されます。"),
                ("<strong>HUD overlay</strong> — per-joint load and temperature bars painted on the render.",
                 "<strong>HUD overlay</strong> — 各ジョイントの負荷と温度のバーを描画上に表示します。"),
                ("<strong>Camera</strong> — shows the USB camera as a card you can move and resize.",
                 "<strong>Camera</strong> — USBカメラを、移動・サイズ変更できるカードとして表示します。"),
                ("<strong>Reset View</strong>, <strong>Reset layout</strong>, and the twin model file (<strong>Browse</strong> + <strong>Load</strong>) for using another MJCF.",
                 "<strong>Reset View</strong>、<strong>Reset layout</strong>、別のMJCFを使うためのモデルファイル（<strong>Browse</strong> + <strong>Load</strong>）。")]) +
            "</div></div>" +
            p("Twin mouse controls: left-drag orbits, right-drag (or Shift+left-drag) pans, the wheel zooms, a double-click resets the view. The twin is rendered at the size of the window, so it stays sharp when you go full screen.",
              "ツインのマウス操作：左ドラッグで回転、右ドラッグ（またはShift+左ドラッグ）で平行移動、ホイールでズーム、ダブルクリックで視点リセット。ツインはウィンドウの大きさで描画されるため、全画面でも鮮明です。")) +
        step(3, ("The Jog card", "Jogカード"),
            fig("card_jog", "Jog card with Joint, World and Tool modes, speed, step and one row per joint", "The Jog card on the SO-101.", "SO-101のJogカード。", "tight") +
            ul([("<strong>Joint / World / Tool</strong> — joint by joint, or Cartesian in the base frame or the tool frame. World and Tool need the twin; a 5-joint arm cannot make every direction, so partly reachable axes are drawn dashed.",
                 "<strong>Joint / World / Tool</strong> — ジョイントごと、またはベース座標系・ツール座標系でのデカルト操作。World / Toolにはツインが必要です。5軸アームはすべての方向を出せないため、一部しか届かない軸は破線で表示されます。"),
                ("Hold <strong>+</strong> or <strong>−</strong> to move; type a value and press Enter to go there. <strong>Step</strong> chooses continuous motion or fixed steps.",
                 "<strong>+</strong> / <strong>−</strong> を押している間だけ動きます。数値を入力してEnterでその位置へ移動。<strong>Step</strong>で連続移動か固定ステップかを選びます。"),
                ("The thin bar under each joint shows where it is inside its calibrated range (blue fill); the cyan tick is the position it has been told to reach.",
                 "各ジョイントの下の細いバーは、キャリブレーション範囲のどこにいるか（青い塗り）を示し、水色の目盛りは指令された目標位置です。"),
                ("<strong>Speed</strong> is one slider: it scales jog speed and, on the B601-DM, also caps how fast the follower chases any target, so teleop and waypoint playback slow down with it (30 % = 0.3 rad/s, never above 0.8 rad/s). In MIT mode it limits how fast the commanded setpoint moves toward a target (30 % = 90°/s), so a far target is approached instead of jumped to.",
                 "<strong>Speed</strong>は1本のスライダーです。ジョグ速度を調整し、B601-DMではフォロワーが目標を追う速度の上限にもなるため、遠隔操作やウェイポイント再生も遅くなります（30% = 0.3 rad/s、上限は0.8 rad/s）。MITモードでは、目標へ向かう指令値の移動速度を制限します（30% = 90°/s）。遠い目標へも一気に飛ばず、滑らかに近づきます。")])) +
        step(4, ("The dock: Connection and Control source", "ドック：ConnectionとControl source"),
            fig("card_dock", "Dock card with the Connection block and four control source tiles", "The dock before anything is connected.", "何も接続していない状態のドック。", "wide") +
            p("<strong>Connection</strong> is for the follower: port, calibration file, Connect, Torque ON / OFF (and <strong>Calibrate gripper range…</strong> on the B601-DM). Once connected, the port and calibration rows fold away to keep the card short. "
              "<strong>Control source</strong> has four tiles — Manual, Leader arm, Gamepad, Keyboard — and only one drives the arm at a time.",
              "<strong>Connection</strong>はフォロワー用です：ポート、キャリブレーションファイル、Connect、Torque ON / OFF（B601-DMでは<strong>Calibrate gripper range…</strong>も）。接続すると、ポートとキャリブレーションの行は畳まれてカードが短くなります。"
              "<strong>Control source</strong>にはManual / Leader arm / Gamepad / Keyboardの4つのタイルがあり、アームを動かせるのは常に1つだけです。")) +
        step(5, ("Move, fold and reset the cards", "カードの移動・折りたたみ・リセット"),
            fig("cards_tabs", "View and Jog cards folded into small tabs at the corners", "View and Jog folded into tabs.", "ViewとJogをタブに折りたたんだ状態。") +
            ul([("<strong>Drag</strong> a card by its dotted handle (or the View title) to put it where you like. The place is saved.",
                 "点線のハンドル（Viewはタイトル）を<strong>ドラッグ</strong>して好きな位置へ。位置は保存されます。"),
                ("The <strong>–</strong> button folds a card into a small tab; click the tab to bring it back.",
                 "<strong>–</strong>ボタンでカードを小さなタブに折りたたみ、タブをクリックすると元に戻ります。"),
                ("Cards never overlap each other or sit under the top bar: a card dropped on another slides to the nearest free spot, and if a small window or an open drawer leaves no room, View and Camera fold themselves into tabs until there is room again.",
                 "カード同士やトップバーとは重なりません。別のカードの上に置くと近くの空きへスライドし、小さなウィンドウやドロワー展開で場所が足りないときはViewとCameraが自動でタブになり、余裕ができると戻ります。"),
                ("The camera card has a resize corner. <strong>Reset layout</strong> (View card) restores everything.",
                 "Cameraカードには右下にサイズ変更用のつまみがあります。<strong>Reset layout</strong>（Viewカード）ですべて初期状態に戻ります。")])) +
        step(6, ("The command palette and shortcuts", "コマンドパレットとショートカット"),
            fig("palette", "Command palette filtering the list for the word source", "Ctrl+K, then type: here the word <em>source</em> narrows the list to the control-source choices.",
                "Ctrl+Kのあと入力：ここでは<em>source</em>と入力してコントロールソースの選択肢に絞り込んでいます。", "tight") +
            table((("Key", "Action"), ("キー", "動作")),
                  [(("Ctrl+K", "Open the command palette"), ("Ctrl+K", "コマンドパレットを開く")),
                   (("Ctrl+Shift+Space", "Stop (torque off, back to Manual)"), ("Ctrl+Shift+Space", "Stop（トルクOFF、Manualに戻す）")),
                   (("Up / Down, Enter, Esc", "Move, run, close inside the palette"), ("↑ / ↓、Enter、Esc", "パレット内での移動・実行・閉じる"))], mono_first=True) +
            callout("good", "✓", "The palette only offers navigation, view toggles, control-source choices and the calibration dialogs. Nothing in it moves an arm, and choosing Leader arm only puts it on standby.",
                    "パレットにあるのは、画面移動、表示の切り替え、コントロールソースの選択、キャリブレーションのダイアログだけです。アームが動くコマンドはなく、Leader armを選んでも待機になるだけです。"))))

    # ---- 02 SO-101 setup
    parts.append(stage("so101", "02", ("Set up the SO-101", "SO-101のセットアップ"),
        why("The SO-101 has to be prepared in order: give every servo its own id, calibrate the arm, then drive it. "
            "All of it lives in the <strong>Setup</strong> hub (top bar → Setup). <strong>Back to stage</strong> returns to the twin.",
            "SO-101は順番どおりに準備します：各サーボにIDを割り当て、アームをキャリブレーションし、その後に操作します。"
            "すべて<strong>Setup</strong>ハブ（トップバー → Setup）にあります。<strong>Back to stage</strong>でツインに戻れます。"),
        fig("setup_so_ids", "Setup hub, Motors and ids, for the SO-101: connect, scan, assign an id, and the arm status table",
            "Setup › Motors and ids on the SO-101: 1 connect, 2 scan, 3 assign; the Arm status table on the right.",
            "SO-101の Setup › Motors and ids：1 接続、2 スキャン、3 ID割り当て。右側はArm statusテーブル。") +
        h3("Motors and ids — give each servo an ID", "Motors and ids — 各サーボにIDを割り当てる") +
        p("Every STS3215 leaves the factory answering to <strong>id 1</strong>. Six of them on one bus are electrically fine but logically identical: a read addressed to id 1 gets six colliding replies. Nothing else works until each servo has its own address.",
          "すべてのSTS3215は工場出荷時に<strong>ID 1</strong>で応答します。6台を1本のバスに繋いでも電気的には問題ありませんが、論理的には区別できません。ID 1宛ての読み取りには6台分の応答が衝突して返ります。各サーボに固有のアドレスを割り当てるまで、他の機能は動きません。") +
        step(1, ("Connect to the bus", "バスに接続する"),
            p("Pick the USB serial adapter's port (the label names the chip: CH340 / CH343 / CP210x / FTDI, which tells it apart from an unrelated COM port) and click <strong>Connect</strong>. No calibration file is needed here.",
              "USBシリアルアダプタのポートを選びます（ラベルにチップ名 CH340 / CH343 / CP210x / FTDI が出るので、無関係なCOMポートと区別できます）。<strong>Connect</strong>をクリック。この段階ではキャリブレーションファイルは不要です。")) +
        step(2, ("Scan for servos", "サーボをスキャンする"),
            p("<strong>Scan bus (all baudrates)</strong> sweeps all eight Feetech baudrates and ids 0–20, so a servo somebody reconfigured still shows up. <strong>Quick rescan (1&nbsp;Mbps)</strong> checks only the rate this app uses. <strong>Deep scan (ids 0–253)</strong> is the last resort for a servo at an unexpected id. "
              "In the <strong>Arm status</strong> table a joint turns <span style='color:var(--good)'>green (OK)</span> when it answers at the expected id and baudrate, <span style='color:var(--warn)'>amber</span> if it is alive on the wrong baudrate, and <span style='color:var(--danger)'>red (missing)</span> if it never answered.",
              "<strong>Scan bus (all baudrates)</strong>はFeetechの8種類のボーレートとID 0〜20を走査するので、以前に別設定にされたサーボも見つかります。<strong>Quick rescan (1 Mbps)</strong>は本アプリが使うレートだけを確認します。<strong>Deep scan (ids 0–253)</strong>は想定外のIDにいるサーボを探す最終手段です。"
              "<strong>Arm status</strong>テーブルでは、想定のIDとボーレートで応答すると<span style='color:var(--good)'>緑（OK）</span>、生きているがボーレートが違うと<span style='color:var(--warn)'>黄色</span>、一度も応答しないと<span style='color:var(--danger)'>赤（missing）</span>になります。")) +
        step(3, ("Assign the connected servo's ID", "接続中のサーボにIDを割り当てる"),
            callout("danger", "⚠", "Plug in <strong>exactly one</strong> servo for this step. Every servo answers to id 1 out of the box, so with several attached they would all take the new id at once and stay indistinguishable. That is why <strong>Assign this id to the connected servo</strong> is enabled only while the scan sees a single servo.",
                    "この手順では<strong>必ず1台だけ</strong>サーボを接続してください。出荷時はすべてID 1で応答するため、複数台を繋ぐと全台が同時に新しいIDを受け取り、区別できないままになります。<strong>Assign this id to the connected servo</strong>が、スキャン結果が1台のときだけ有効になるのはそのためです。") +
            p("Choose which joint this servo becomes (the first joint still missing is preselected, so going 1→6 needs no fiddling) and click Assign. A servo on the wrong baudrate can be moved back to 1&nbsp;Mbps with <strong>Set servo baudrate</strong>. Repeat with the next raw servo until all six are OK.",
              "このサーボをどのジョイントにするか選びます（まだ不足している最初のジョイントが自動選択されるので、1→6の順なら操作不要）。Assignをクリック。ボーレートが違うサーボは<strong>Set servo baudrate</strong>で1 Mbpsに戻せます。次の未設定サーボで繰り返し、6台すべてがOKになるまで続けます。") +
            callout("good", "✓", "This is the in-app equivalent of <code>lerobot-setup-motors</code>.", "これは <code>lerobot-setup-motors</code> のアプリ内版に相当します。")) +
        h3("Calibration — record what “middle” and “as far as it goes” mean", "Calibration — 「中央」と「可動範囲の限界」を記録する") +
        fig("setup_so_cal", "Setup, Calibration, on the SO-101: joint checklist, role, port and the five gated step buttons", "Setup › Calibration on the SO-101 (before connecting).", "SO-101の Setup › Calibration（接続前）。") +
        p("A servo's raw encoder count (0–4095) says nothing about the joint's real position until two things are fixed: where its <em>zero</em> is (the homing offset) and how far it may travel (min / max limits). Calibration records both per joint into one LeRobot-format <code>.json</code>, following <code>lerobot-calibrate</code>'s own sequence.",
          "サーボの生のエンコーダ値（0〜4095）は、<em>ゼロ点</em>（ホーミングオフセット）と可動範囲（最小・最大リミット）が決まるまで、ジョイントの実際の位置を意味しません。Calibrationはこの2つをジョイントごとにLeRobot形式の<code>.json</code>へ記録します。手順は<code>lerobot-calibrate</code>と同じです。") +
        p("Choose the <strong>Role</strong> (Follower or Leader — a leader needs its own file), untick any joint that is not plugged in, pick the port and Connect. The five buttons unlock one at a time; <strong>Reset</strong> is always clickable as the way to start over.",
          "<strong>Role</strong>（FollowerかLeader。リーダーにも専用ファイルが必要）を選び、接続していないジョイントのチェックを外し、ポートを選んでConnectします。5つのボタンは1つずつ解放され、<strong>Reset</strong>だけは「やり直し」用に常にクリックできます。") +
        table((("#", "Step", "What to do"), ("#", "ステップ", "操作")),
              [(("1", "Reset motors", "Clears previous homing offsets and limits."), ("1", "Reset motors", "以前のホーミングオフセットとリミットをクリアします。")),
               (("2", "Set middle", "By hand, park every joint near the middle of its travel, then click. A picture of the twin's own zero pose is shown first."), ("2", "Set middle", "全ジョイントを手で可動範囲の中央付近に置いてクリック。先にツインのゼロ姿勢の参考画像が表示されます。")),
               (("3", "Start recording", "Begins tracking min / max ticks per joint."), ("3", "Start recording", "ジョイントごとの最小・最大ティックの記録を開始します。")),
               (("4", "Stop recording", "First move every joint slowly through its FULL range (wrist_roll is treated as a full turn automatically)."), ("4", "Stop recording", "先に全ジョイントを可動範囲いっぱいまでゆっくり動かします（wrist_rollは自動で1回転分として扱われます）。")),
               (("5", "Finish &amp; Save", "Writes the limits to the servos and asks where to save the .json (default: LeRobot's own folder)."), ("5", "Finish &amp; Save", "リミットをサーボに書き込み、.jsonの保存先を尋ねます（既定はLeRobotのフォルダ）。"))], mono_first=True) +
        callout("warn", "⚠", "<strong>The gripper needs care.</strong> The sweep assumes the joint's hard stop is its real limit. A belt- or rack-driven gripper runs into a freewheel zone past its real extremes, so the sweep records a near-360° range. Use one of the two helpers under the step buttons: <strong>manual 2-point capture</strong> (park it fully closed, click <em>Capture gripper CLOSED</em>, then fully open) or <strong>Auto-calibrate (stall-safe)</strong>, which drives to both extremes at reduced torque and stops the moment it stalls. Do not disconnect during Auto-calibrate: restoring full torque mid-search can leave the goal past the real wall.",
                "<strong>グリッパーには注意が必要です。</strong>スイープは、ジョイントの物理ストッパーが実際の限界だと仮定します。ベルト駆動やラック駆動のグリッパーは実際の限界を超えるとフリーホイール状態になり、ほぼ360°の範囲が記録されてしまいます。ステップボタンの下にある2つの方法を使ってください：<strong>manual 2-point capture</strong>（完全に閉じた状態で<em>Capture gripper CLOSED</em>、次に完全に開いた状態で同様に）、または<strong>Auto-calibrate (stall-safe)</strong>（低トルクで両端まで動き、失速した瞬間に停止）。Auto-calibrate中に切断しないでください。探索の途中でフルトルクに戻ると、目標が実際の壁の先に残る場合があります。") +
        p("Keep the saved file: you point the Connection card at it every session. Files are interchangeable with <code>lerobot-teleoperate</code> and <code>lerobot-record</code> in both directions.",
          "保存したファイルは大切に保管してください。セッションごとにConnectionカードでこのファイルを指定します。ファイルは<code>lerobot-teleoperate</code>や<code>lerobot-record</code>と双方向に共有できます。") +
        h3("Inputs, and Data and logs", "Inputs と Data and logs") +
        fig("setup_inputs", "Setup, Inputs: gamepad status and the keyboard jog key layout", "Setup › Inputs: gamepad mapping and the keyboard jog keys.", "Setup › Inputs：ゲームパッドの割り当てとキーボードジョグのキー。") +
        p("<strong>Inputs</strong> shows the gamepad status and mapping, and the keyboard jog key caps, which light up while a key is held. <strong>Data and logs</strong> shows where this session's log file is and opens its folder; the telemetry CSV log is started from the Telemetry drawer.",
          "<strong>Inputs</strong>にはゲームパッドの状態と割り当て、キーボードジョグのキーキャップ（押している間点灯）があります。<strong>Data and logs</strong>ではこのセッションのログファイルの場所を確認し、フォルダを開けます。テレメトリのCSVログはTelemetryドロワーから開始します。") +
        fig("setup_logs", "Setup, Data and logs: the session log path and an Open log folder button", "Setup › Data and logs.", "Setup › Data and logs。", "tight")))

    # ---- 03 B601-DM setup
    parts.append(stage("b601", "03", ("Set up the reBot B601-DM", "reBot B601-DMのセットアップ"),
        why("The B601-DM is a different machine underneath: Damiao motors on a CAN bus, and a leader (Star Arm 102) that is not Damiao at all but FashionStar UART servos on its own USB port. Switch the Robot picker to <strong>reBot B601-DM</strong>; the Setup hub then shows the Damiao tools.",
            "B601-DMは内部が別物です：CANバス上のDamiaoモーターと、Damiaoではなく専用USBポートのFashionStar UARTサーボで構成されたリーダー（Star Arm 102）です。Robot選択を<strong>reBot B601-DM</strong>に切り替えると、SetupハブにDamiao用のツールが表示されます。"),
        fig("stage_dm", "The stage on the reBot B601-DM", "The stage on the reBot B601-DM.", "reBot B601-DMのステージ。") +
        h3("Motors and ids — CAN ids", "Motors and ids — CANのID") +
        fig("setup_dm_ids", "Setup, Motors and ids for the B601-DM: connect, probe, assign, motor state and PID, and the motor status table",
            "Setup › Motors and ids on the B601-DM: 1 connect, 2 probe, 3 assign, 4 motor state &amp; PID, and the Motor status table.",
            "B601-DMの Setup › Motors and ids：1 接続、2 プローブ、3 割り当て、4 モーターの状態とPID、右はMotor statusテーブル。") +
        p("Every Damiao motor ships answering to the same default id, so several on one bus cannot be told apart. Give each its own id, <strong>one motor at a time</strong>: the CAN id is the motor's 1-based position in the arm (joint1 = 01 … finger_left = 07) and the master id is the CAN id + 0x10.",
          "Damiaoのモーターは出荷時にすべて同じIDで応答するため、複数台を1本のバスに繋ぐと区別できません。<strong>1台ずつ</strong>固有のIDを割り当てます。CAN IDはアーム内での1始まりの位置（joint1 = 01 … finger_left = 07）、マスターIDはCAN ID + 0x10です。") +
        callout("danger", "⚠", "<strong>No hot-plugging.</strong> Cut power before connecting or disconnecting the XT30 2+2 interface, as Seeed's own safety notes say. Unlike Feetech's servos, this connector is not rated for it.",
                "<strong>活線挿抜は禁止です。</strong>Seeed公式の安全上の注意のとおり、XT30 2+2コネクタを抜き差しする前に電源を切ってください。Feetechのサーボと違い、このコネクタは活線挿抜に対応していません。") +
        step(1, ("Connect to the adapter", "アダプタに接続する"), p("Pick the USB-serial adapter Damiao's own DM_Tools uses (921&nbsp;600 baud, fixed) and Connect.", "Damiao純正DM_Toolsと同じUSBシリアルアダプタを選び（921 600 baud固定）、Connectします。")) +
        step(2, ("Probe the one connected motor", "接続した1台をプローブ"), p("Enter the motor's current id in hex (usually <code>01</code>) and click <strong>Probe</strong>. With one motor on the bus it answers &quot;found&quot;.", "モーターの現在のIDを16進数で入力し（通常は<code>01</code>）、<strong>Probe</strong>をクリック。バス上が1台なら「found」と応答します。")) +
        step(3, ("Give it a unique id", "固有のIDを割り当てる"), p("Pick which joint it is; the suggested id and master id are filled in. <strong>Assign to the connected motor</strong> asks for confirmation and writes the motor's flash. Unplug it, chain the next motor on, and repeat.", "どのジョイントか選ぶと、推奨のIDとマスターIDが入力されます。<strong>Assign to the connected motor</strong>は確認のあとモーターのフラッシュに書き込みます。取り外して次のモーターを繋ぎ、繰り返します。")) +
        step(4, ("Verify the whole arm", "アーム全体を検証"), p("With all seven wired, <strong>Verify All</strong> reads every id back in one pass; the Motor status table shows OK or NO RESPONSE per joint.", "7台すべてを配線した状態で<strong>Verify All</strong>を押すと、全IDを一度に読み戻し、Motor statusテーブルにジョイントごとのOKまたはNO RESPONSEが表示されます。")) +
        p("Section 4 (<strong>Motor state &amp; PID</strong>) works on the motor the last Probe found: <strong>Enable</strong> and <strong>Disable</strong> the motor's own loop, <strong>Set Zero</strong> to save its current pose as its zero in flash (park the arm where you want zero first), and <strong>Read PID</strong> / <strong>Write PID</strong> for the onboard gains. Write PID stays disabled until a Read succeeds, so fields showing 0 can never be written by accident.",
          "セクション4（<strong>Motor state &amp; PID</strong>）は、直近のProbeで見つかったモーターに作用します：<strong>Enable</strong> / <strong>Disable</strong>でモーター自身の制御ループを入切、<strong>Set Zero</strong>で現在の姿勢をゼロ点としてフラッシュに保存（先に、ゼロにしたい位置へアームを置いてください）、<strong>Read PID</strong> / <strong>Write PID</strong>で内蔵ゲインを読み書きします。Write PIDはReadに成功するまで無効なので、0表示のまま書き込んでしまうことはありません。") +
        h3("Calibration — the gripper and the leader", "Calibration — グリッパーとリーダー") +
        fig("setup_dm_cal", "Setup, Calibration for the B601-DM: follower gripper range, leader Star Arm 102, and zero position cards", "Setup › Calibration on the B601-DM.", "B601-DMの Setup › Calibration。") +
        p("The B601-DM does not use the Feetech wizard. Its Calibration page has cards that open read-only sweeps: you move the arm by hand and the app only watches.",
          "B601-DMではFeetech用のウィザードは使いません。Calibrationページには、読み取り専用のスイープを開くカードがあります。アームは手で動かし、アプリは観測するだけです。") +
        ul([("<strong>Follower gripper range</strong> — with the follower connected and torque <strong>off</strong>, sweep the jaw open to closed by hand. It stops the app from ever driving the gripper past its real end stop (a held jog past it once stalled the motor until a fuse blew). The dialog refuses to run while torque is on. Also reachable from <em>Calibrate gripper range…</em> in the Connection card. Reconnect the follower afterwards to apply the new range.",
             "<strong>Follower gripper range</strong> — フォロワーを接続し、トルクを<strong>OFF</strong>にした状態で、ジョーを手で開から閉まで動かします。グリッパーが実際のエンドストップを超えて駆動されるのを防ぎます（ジョグを押し続けてモーターが失速し、ヒューズが切れた事例があります）。トルクONの間はダイアログが起動しません。ConnectionカードのCalibrate gripper range…からも開けます。適用にはフォロワーの再接続が必要です。"),
            ("<strong>Leader — Star Arm 102</strong> — with the leader connected, sweep every joint through its full travel. It replaces the vendor default ranges, which were off by up to 1.75× on the unit this was built with. Reconnect the leader afterwards.",
             "<strong>Leader — Star Arm 102</strong> — リーダーを接続し、全ジョイントを可動範囲いっぱいまで動かします。ベンダー既定の範囲（このアプリを作った個体では最大1.75倍ずれていました）を置き換えます。適用にはリーダーの再接続が必要です。"),
            ("<strong>Zero position</strong> — a pointer to Set Zero in Motors and ids (section 4), because it works on the probed motor.",
             "<strong>Zero position</strong> — Set Zeroは Probe したモーターに作用するため、Motors and ids（セクション4）への案内です。")]) +
        p("The Star Arm 102 needs no calibration file to connect: its zero lives in the servos' own flash after a one-time <code>set_origin_point()</code>, equivalent to <code>lerobot-calibrate</code>'s step for this leader. Connect it from the Control source tile <strong>Leader arm</strong>.",
          "Star Arm 102は接続にキャリブレーションファイルを必要としません。ゼロ点は一度<code>set_origin_point()</code>を実行するとサーボ自身のフラッシュに保存されます（このリーダーに対する<code>lerobot-calibrate</code>の手順に相当）。コントロールソースの<strong>Leader arm</strong>タイルから接続します。")))

    # ---- 04 drive
    parts.append(stage("drive", "04", ("Drive it", "動かす"),
        why("Back on the stage. Connect the follower, turn torque on, choose who drives it. The app refuses to connect the SO-101 without a calibration file: there is no “just let me move it” mode.",
            "ステージに戻ります。フォロワーに接続し、トルクをONにして、誰が動かすかを選びます。SO-101はキャリブレーションファイルなしでは接続できません（「とりあえず動かす」モードはありません）。"),
        step(1, ("Connect and torque on", "接続とトルクON"),
            p("In the Connection card pick the port and (SO-101) the calibration <code>.json</code> you saved, click <strong>Connect</strong>, then <strong>Torque ON</strong>. Torque-on seeds the goal with the current measured pose, so the arm holds still instead of lurching to an old target. On the B601-DM the connection uses the ids you assigned and the mode chosen in the Tune drawer. "
              "The red <strong>Stop</strong> in the top bar is always there.",
              "Connectionカードでポートと（SO-101では）保存したキャリブレーション<code>.json</code>を選び、<strong>Connect</strong>、続けて<strong>Torque ON</strong>をクリックします。Torque ONは現在の実測姿勢を目標に設定するので、古い目標へ急に動かず、その場で保持します。B601-DMでは、割り当てたIDとTuneドロワーで選んだモードで接続します。トップバーの赤い<strong>Stop</strong>は常に使えます。")) +
        step(2, ("Pick a control source", "コントロールソースを選ぶ"),
            ul([("<strong>Manual</strong> — the Jog card.", "<strong>Manual</strong> — Jogカード。"),
                ("<strong>Gamepad</strong> — a standard Xbox-style controller, one stick axis per joint.", "<strong>Gamepad</strong> — 標準的なXbox系コントローラー。スティックの各軸が1ジョイントに対応。"),
                ("<strong>Keyboard</strong> — hold a key, the joint moves at a constant rate; release, it stops. Keys held when the window loses focus are released automatically. Keys (SO-101): Q/A shoulder_pan, W/S shoulder_lift, E/D elbow_flex, R/F wrist_flex, T/G wrist_roll, Y/H gripper.",
                 "<strong>Keyboard</strong> — キーを押している間、そのジョイントが一定速度で動き、離すと止まります。ウィンドウがフォーカスを失うと押下中のキーは自動で解放されます。キー（SO-101）：Q/A shoulder_pan、W/S shoulder_lift、E/D elbow_flex、R/F wrist_flex、T/G wrist_roll、Y/H gripper。"),
                ("<strong>Leader arm</strong> — a second arm you move by hand drives the follower. See the next step.", "<strong>Leader arm</strong> — 手で動かす2台目のアームがフォロワーを動かします。次のステップを参照。")])) +
        step(3, ("Leader arm: standby, align, engage", "Leader arm：待機・位置合わせ・Engage"),
            p("Choosing <strong>Leader arm</strong> connects the leader (SO-101: a second arm with its own port and calibration file; B601-DM: the Star Arm 102) but <strong>does not move the follower</strong>. The follower only tracks the leader after you press <strong>Engage teleop</strong>, and Engage is refused until the arms are close, so it can never snap to wherever the leader happens to be.",
              "<strong>Leader arm</strong>を選ぶとリーダーに接続しますが、<strong>フォロワーは動きません</strong>。<strong>Engage teleop</strong>を押して初めてフォロワーがリーダーを追従します。しかも両アームが近くなるまでEngageは拒否されるので、リーダーの現在位置へ急に飛ぶことはありません。") +
            fig("gate_far", "Leader on standby: the ghost shows the leader pose, the alignment bar is red and Engage teleop is greyed out", "Standby, arms far apart: the ghost is the leader's pose, the bar is red, and the message names the joint that is off.", "待機中で両アームが離れている状態：ゴーストがリーダーの姿勢、バーは赤、メッセージにずれているジョイントが表示されます。") +
            fig("gate_ok", "Leader on standby with the arms aligned: green alignment bar and Engage teleop enabled", "Aligned: every bar is green and Engage teleop is enabled.", "位置が揃った状態：すべてのバーが緑になり、Engage teleopが有効になります。") +
            ul([("Engage is enabled only when the leader and the follower are both online, <strong>follower torque is ON</strong>, and every arm joint is within the <strong>Tolerance</strong> (default 10°, saved). The gripper is not part of the check.",
                 "Engageが有効になるのは、リーダーとフォロワーの両方がオンライン、<strong>フォロワーのトルクがON</strong>、かつすべてのアーム関節が<strong>Tolerance</strong>（既定10°、保存されます）以内のときだけです。グリッパーは判定に含まれません。"),
                ("While on standby the leader's pose is drawn as the ghost: move the leader until the ghost sits on the arm, then engage.",
                 "待機中はリーダーの姿勢がゴーストで表示されます。ゴーストがアームに重なるまでリーダーを動かしてから、Engageします。"),
                ("Teleop ends by itself on <strong>Stop</strong>, torque OFF, changing the control source, or either arm disconnecting. The button turns red as <strong>Disengage</strong> while it runs.",
                 "<strong>Stop</strong>、トルクOFF、コントロールソースの変更、どちらかのアームの切断で、遠隔操作は自動的に終了します。動作中、ボタンは赤い<strong>Disengage</strong>になります。"),
                ("<strong>Gripper moves the wrong way (invert)</strong> flips the direction the relay treats as opening; <strong>Relay trim…</strong> adds a constant per-joint correction in degrees, most often needed on wrist_roll, whose zero is wherever the arm was held during Set middle. The two arms are calibrated independently, so the relay maps by fraction of each arm's own range, not raw degrees.",
                 "<strong>Gripper moves the wrong way (invert)</strong>は、リレーが「開く」とみなす向きを反転します。<strong>Relay trim…</strong>はジョイントごとに一定の補正角度（度）を加えます。wrist_rollで最も必要になります（ゼロ点がSet middle時の位置になるため）。2台は独立にキャリブレーションされているため、リレーは生の角度ではなく各アームの可動範囲に対する割合で対応づけます。")]) +
            fig("dock_gate", "The dock in Leader arm mode: connection, leader port, Engage teleop, tolerance and the alignment bar", "The dock in Leader arm mode.", "Leader armモードのドック。", "wide")) +
        step(4, ("Teach a sequence (Waypoints drawer)", "シーケンスを記録する（Waypointsドロワー）"),
            fig("drawer_waypoints", "Waypoints drawer with three waypoints, one with a delay, and the ghost of the selected waypoint on the twin", "The Waypoints drawer. The selected waypoint is drawn as the ghost.", "Waypointsドロワー。選択中のウェイポイントがゴーストとして表示されます。") +
            p("<strong>Record Waypoint</strong> snapshots the follower's current pose however it got there (hand-guided with torque off, leader-driven, or jogged). <strong>Record Grip</strong> is for a waypoint that must hold something: it snaps the gripper value to its nearest calibrated extreme so playback keeps commanding a target the object will not let it reach — that position error is the grip force. Reorder with Move Up / Down, then <strong>Play Sequence</strong> replays at a capped, eased speed (zero velocity and acceleration at each waypoint).",
              "<strong>Record Waypoint</strong>は、フォロワーの現在の姿勢を（トルクOFFで手で動かした場合も、リーダー操作もジョグも）そのまま記録します。<strong>Record Grip</strong>は何かを掴んで保持するウェイポイント用で、グリッパー値を最寄りのキャリブレーション端点にスナップします。再生中は対象物が許さない目標を出し続けるため、その位置誤差が把持力になります。Move Up / Downで順序を変え、<strong>Play Sequence</strong>で上限速度付きの滑らかな動きで再生します（各ウェイポイントで速度・加速度ゼロ）。") +
            ul([("<strong>Delay after selected waypoint</strong> + <strong>Set Delay on Selected</strong> pauses playback at that waypoint (e.g. hold a grip for 800&nbsp;ms).", "<strong>Delay after selected waypoint</strong> と <strong>Set Delay on Selected</strong> で、そのウェイポイントで再生を一時停止します（例：把持を800ms保持）。"),
                ("<strong>Loop</strong> restarts from waypoint 1; unticking it mid-run lets the current pass finish. <strong>Speed</strong> (deg/s) can be changed live. <strong>Save…</strong> / <strong>Load…</strong> use <code>.json</code> files.", "<strong>Loop</strong>は終了後にウェイポイント1から再開します。実行中に外すと現在の周回を終えて止まります。<strong>Speed</strong>（deg/s）は実行中も変更可能。<strong>Save…</strong> / <strong>Load…</strong>は<code>.json</code>を使います。")])) +
        step(5, ("Read the servo telemetry (Telemetry drawer)", "サーボテレメトリを読む（Telemetryドロワー）"),
            fig("drawer_telemetry", "Telemetry drawer with the table of position, velocity, load, current, voltage and temperature per joint", "The Telemetry drawer (values here are made up).", "Telemetryドロワー（数値はダミー）。") +
            p("Current, load, velocity, voltage and temperature per joint at about 10&nbsp;Hz, as a table or a live Graph. <strong>Watch</strong> a joint and field for running min / max / mean, and <strong>Start CSV Log</strong> to capture a session. The caption under the table says which values are cross-checked vendor conversions and which is an estimate (<code>Vel (deg/s)*</code>, marked with a star). On the SO-101, <strong>Target / Torque_Limit / Dead_Zone</strong> at the bottom tunes a joint's servo-side output cap and deadband; a joint that stalls under payload during playback usually just needs more Torque_Limit.",
              "各ジョイントの電流・負荷・速度・電圧・温度を約10Hzで、テーブルまたはライブグラフで表示します。<strong>Watch</strong>でジョイントと項目を選ぶと最小・最大・平均が更新され、<strong>Start CSV Log</strong>でセッションを記録できます。テーブル下のキャプションに、検証済みのメーカー換算値と推定値（<code>Vel (deg/s)*</code>、星印）の区別が書かれています。SO-101では下部の<strong>Target / Torque_Limit / Dead_Zone</strong>でサーボ側の出力上限とデッドバンドを調整できます。再生中に負荷で失速するジョイントは、たいていTorque_Limitを上げるだけで解決します。")) +
        step(6, ("Tune the B601-DM (Tune drawer)", "B601-DMを調整する（Tuneドロワー）"),
            fig("drawer_tune", "Tune drawer with control mode, kp and kd per joint, gain presets and the tracking chart", "The Tune drawer: mode and gains, presets, and the tracking chart (synthetic data).", "Tuneドロワー：モードとゲイン、プリセット、トラッキングチャート（模擬データ）。") +
            ul([("<strong>Control mode</strong> — <strong>POS_VEL</strong> (default) uses the motor's own position / velocity loop. <strong>MIT</strong> sends fresh stiffness / damping (<code>kp</code>, <code>kd</code>) with every command: more responsive and compliant, but the gains must be tuned on the real arm, so start low (kp 5–10, kd 0.3–0.8). The mode applies on the next Connect; kp / kd apply <strong>live</strong>, with no reconnect and no torque cycling.",
                 "<strong>Control mode</strong> — <strong>POS_VEL</strong>（既定）はモーター自身の位置・速度ループを使います。<strong>MIT</strong>は毎回のコマンドで剛性・減衰（<code>kp</code>、<code>kd</code>）を送ります。応答性が高く柔らかい反面、ゲインは実機で調整が必要なので低い値（kp 5〜10、kd 0.3〜0.8）から始めてください。モードは次回のConnectで反映され、kp / kdは再接続やトルクの入れ直しなしに<strong>即時</strong>反映されます。"),
                ("<strong>Gain presets</strong> — <em>Save as…</em> stores the current kp / kd under a name, <em>Load</em> fills the boxes (and a connected MIT follower applies them at once), <em>Delete</em> removes one. Two are built in: &quot;Gentle start&quot; (the safe default) and &quot;Holding start (stiffer, for load)&quot; (kp 40 / kd 2.5 on joint2 and joint3). The second is a first guess for arms that must hold weight: load it with the arm supported and a hand near Stop, then tune each joint.",
                 "<strong>Gain presets</strong> — <em>Save as…</em>で現在のkp / kdを名前付きで保存、<em>Load</em>で入力欄に反映（MIT接続中のフォロワーには即時適用）、<em>Delete</em>で削除。組み込みは2つ：「Gentle start」（安全な既定値）と「Holding start (stiffer, for load)」（joint2・joint3は kp 40 / kd 2.5）。後者は荷重を保持するアーム向けの初期値の目安です。アームを支え、手をStopの近くに置いてから読み込み、ジョイントごとに調整してください。"),
                ("<strong>Gravity compensation</strong> — in MIT the motor only receives kp / kd, so a loaded joint sags by about torque / kp. This adds the arm's own weight, computed from the MuJoCo model, as feed-forward torque so kp does not have to carry it. It is <strong>off</strong> at every start and stays locked until <em>Check gravity model</em> passes: with MIT mode, torque ON and the arm still, the model's holding torques must agree in sign (and roughly in size) with what the motors report. Then raise <em>Amount</em> slowly; it ramps in over a few seconds and is capped per joint (12 N·m for joints 1–3, 4 N·m for joints 4–6; none for the gripper). It switches off by itself on Stop, torque OFF or a disconnect, and then needs a new check.",
                 "<strong>重力補償</strong> — MITではモーターにkp / kdしか渡されないため、負荷のあるジョイントは約「トルク ÷ kp」だけ下がります。ここではMuJoCoモデルから計算したアーム自身の重さを、フィードフォワードトルクとして加え、kpが重さを支えなくて済むようにします。起動のたびに<strong>OFF</strong>で、<em>Check gravity model</em>に合格するまでロックされています。MITモード・トルクON・アームが静止した状態で、モデルの保持トルクがモーターの報告値と符号（と大きさ）で一致する必要があります。その後<em>Amount</em>をゆっくり上げてください。数秒かけて立ち上がり、ジョイントごとに上限があります（joint1〜3は12 N·m、joint4〜6は4 N·m、グリッパーは対象外）。Stop・トルクOFF・切断で自動的にOFFになり、再度チェックが必要です。"),
                ("<strong>Tracking</strong> — pick a joint: the chart shows the commanded target (dashed) against the measured position for the last 10 seconds, with peak error, settle time and overshoot of the latest step. It only reads data.",
                 "<strong>Tracking</strong> — ジョイントを選ぶと、直近10秒の指令目標（破線）と実測位置がチャートに表示され、最新のステップの最大誤差・整定時間・オーバーシュートが出ます。データを読むだけです。"),
                ("<strong>Nudge ±5°</strong> — moves the chosen joint 5° from its current goal, clamped to its calibrated range. It is enabled only with the follower connected, torque ON, control source Manual, teleop not engaged and no sequence playing; otherwise the panel says why it is off.",
                 "<strong>Nudge ±5°</strong> — 選択したジョイントを現在の目標から5°動かします（キャリブレーション範囲内に制限）。フォロワー接続中、トルクON、コントロールソースがManual、遠隔操作が未Engage、シーケンス再生中でない場合のみ有効で、無効なときは理由が表示されます。")]) +
            callout("warn", "⚠", "Raising kp on a joint that carries load can make it oscillate or hit hard. Change one joint at a time, in small steps, with a hand near Stop. A motor that faults after a stall stays latched until its power supply is cycled.",
                    "負荷のかかるジョイントでkpを上げると振動したり、強く当たったりすることがあります。1ジョイントずつ、少しずつ変更し、手をStopの近くに置いてください。失速後にフォルトしたモーターは、電源を入れ直すまでラッチされたままです。"))))

    # ---- Safety
    parts.append(stage("safety", "", ("Safety", "安全上の注意"), "", (
        p("This app is deliberately opinionated about not moving hardware it does not understand:", "本アプリは、把握していないハードウェアを動かさないという方針を意図的に貫いています：") +
        ul([("<strong>The SO-101 refuses to connect without a calibration file</strong>, and every commanded position is <strong>clamped to the calibrated range</strong> before it reaches a servo (and, on the B601-DM, to the calibrated joint range in degrees).",
             "<strong>SO-101はキャリブレーションファイルなしでは接続を拒否</strong>し、送信されるすべての目標位置はサーボに届く前に<strong>キャリブレーション範囲へクランプ</strong>されます（B601-DMでは度単位の較正済み範囲へ）。"),
            ("<strong>Torque-on seeds the goal with the current measured pose</strong>, so re-enabling torque holds still.", "<strong>Torque ONは現在の実測姿勢を目標に設定</strong>するため、トルクを入れ直しても動き出しません。"),
            ("<strong>Stop</strong> (top bar or Ctrl+Shift+Space) turns follower torque off and returns control to Manual, so a leader that is still moving cannot make the arm jump when torque comes back.",
             "<strong>Stop</strong>（トップバーまたはCtrl+Shift+Space）はフォロワーのトルクをOFFにしてManualに戻します。リーダーが動き続けていても、トルク再開時にアームが飛ぶことはありません。"),
            ("<strong>Teleop needs an explicit, alignment-gated Engage</strong> and ends by itself on Stop, torque off, a source change or a disconnect.", "<strong>遠隔操作は、位置合わせを条件とする明示的なEngageが必要</strong>で、Stop・トルクOFF・ソース変更・切断で自動的に終了します。"),
            ("<strong>Id assignment needs exactly one servo (or motor) on the bus</strong>; calibration steps are gated in order and finishing without a completed recording is refused.", "<strong>ID割り当てはバス上に1台だけの状態で</strong>実行します。キャリブレーションは順序どおりにロックされ、記録が未完了のままFinishしようとすると拒否されます。"),
            ("The B601-DM gripper range is measured by hand with torque off, and the calibration dialog will not run while torque is on.", "B601-DMのグリッパー範囲はトルクOFFで手動測定し、トルクONの間はキャリブレーションのダイアログが起動しません。")]) +
        callout("warn", "⚡", "The SO-101's servos need their own <strong>5&nbsp;V power supply</strong>: USB alone runs the serial adapter, not the motors. An arm that connects fine but never moves, with no error, almost always means this. For the B601-DM, never plug or unplug the motor connectors with power on.",
                "SO-101のサーボには専用の<strong>5V電源</strong>が必要です。USB給電だけではシリアルアダプタが動くだけで、モーターは動きません。接続できるのに全く動かず、エラーも出ない場合は、ほぼこれが原因です。B601-DMでは、電源を入れたままモーターのコネクタを抜き差ししないでください。"))))

    # ---- Troubleshooting
    parts.append(stage("troubleshooting", "", ("Troubleshooting", "トラブルシューティング"), "", (
        table((("Symptom", "Likely cause"), ("症状", "主な原因")),
              [(("Port opens but nothing moves (SO-101)", "Missing 5&nbsp;V servo power, or Torque OFF."), ("ポートは開くが何も動かない（SO-101）", "5V電源が未接続、またはTorque OFFのまま。")),
               (("&quot;Access denied&quot; opening the port", "Another section (Setup, Calibration, or the stage's Connect) is already holding it — only one may at a time. Disconnect there first."), ("ポートを開くと「Access denied」", "別の画面（Setup、Calibration、ステージのConnect）がすでに保持しています。同時に使えるのは1つだけなので、先に切断してください。")),
               (("Engage teleop is greyed out", "Read the line under the button: leader or follower offline, torque OFF, or a joint is farther than the Tolerance. Move the leader until the ghost sits on the arm."), ("Engage teleopがグレーのまま", "ボタン下のメッセージを確認：リーダー／フォロワーがオフライン、トルクOFF、またはToleranceを超えるジョイントがあります。ゴーストがアームに重なるまでリーダーを動かしてください。")),
               (("Nudge buttons are greyed out (B601-DM)", "The Tune drawer says why: follower not connected, torque OFF, control source not Manual, teleop engaged, or a sequence is playing."), ("Nudgeボタンがグレー（B601-DM）", "Tuneドロワーに理由が表示されます：フォロワー未接続、トルクOFF、コントロールソースがManualでない、遠隔操作中、シーケンス再生中。")),
               (("A card disappeared", "It is folded into a tab (look at the corners) — click the tab. <em>Reset layout</em> in the View card puts every card back."), ("カードが消えた", "タブに折りたたまれています（四隅を確認）。タブをクリックしてください。Viewカードの<em>Reset layout</em>ですべて元に戻ります。")),
               (("Gripper direction flips vs. the leader", "Switch on &quot;Gripper moves the wrong way (invert)&quot; in the Leader arm block."), ("グリッパーの開閉がリーダーと逆", "Leader armのブロックで「Gripper moves the wrong way (invert)」をオンにしてください。")),
               (("A joint stalls only under load during playback (SO-101)", "Raise that joint's Torque_Limit in the Telemetry drawer."), ("再生中、負荷時だけ失速する（SO-101）", "Telemetryドロワーで該当ジョイントのTorque_Limitを上げてください。")),
               (("Scan finds more than one servo during id assignment", "Unplug all but the one you are assigning — Assign stays disabled until exactly one is found."), ("ID割り当て中のスキャンで複数のサーボが見つかる", "割り当てる1台を除いてすべて外してください。ちょうど1台になるまでAssignは無効です。")),
               (("B601-DM motor stopped answering after a stall", "The motor latched a fault. Cycle its power supply; the app cannot clear it."), ("B601-DMのモーターが失速後に応答しない", "モーターがフォルトをラッチしています。電源を入れ直してください（アプリからは解除できません）。")),
               (("The twin is blank or frozen", "Check the model path in the View card (Browse + Load). Joint names in a custom MJCF must match the robot's."), ("ツインが空白または固まっている", "Viewカードのモデルパス（Browse + Load）を確認してください。独自のMJCFではジョイント名がロボットのものと一致する必要があります。"))]) +
        p("For anything not covered here, the repository's <code>docs/TROUBLESHOOTING.md</code> is indexed by symptom and more exhaustive.", "ここにない症状は、リポジトリの<code>docs/TROUBLESHOOTING.md</code>（症状別・より詳細）を参照してください。"))))

    # ---- About
    parts.append(stage("about", "", ("About", "このアプリについて"), "", (
        fig("about", "About dialog with the creator, version, licence, supported robots and the tech stack with installed versions", "The About dialog (i button in the top bar).", "Aboutダイアログ（トップバーの i ボタン）。", "tight") +
        p("Created by <strong>Haikal Baiqunni</strong>. Built with Python, PySide6 (Qt 6), MuJoCo, NumPy, OpenCV, pySerial, the Feetech servo SDK, <code>motorbridge-smart-servo</code> (FashionStar Star Arm 102), a vendored Damiao CAN driver and pygame. The bundled SO-101 twin model is TheRobotStudio's SO-ARM100 MJCF (Apache-2.0). MIT licensed.",
          "作者：<strong>Haikal Baiqunni</strong>。使用技術：Python、PySide6（Qt 6）、MuJoCo、NumPy、OpenCV、pySerial、Feetech servo SDK、<code>motorbridge-smart-servo</code>（FashionStar Star Arm 102）、同梱のDamiao CANドライバ、pygame。同梱のSO-101ツインモデルはTheRobotStudioのSO-ARM100 MJCF（Apache-2.0）です。MITライセンス。"))))

    return "\n".join(parts)


NAV = [("start", "·", "Before you start", "はじめに"), ("screen", "01", "The screen", "画面"),
       ("so101", "02", "SO-101 setup", "SO-101セットアップ"), ("b601", "03", "B601-DM setup", "B601-DMセットアップ"),
       ("drive", "04", "Drive it", "動かす"), ("safety", "·", "Safety", "安全"),
       ("troubleshooting", "·", "Troubleshooting", "トラブル"), ("about", "·", "About", "About")]

SCRIPT = """
(function(){
  var root = document.documentElement;
  function apply(lang){
    root.setAttribute('data-lang', lang);
    document.querySelectorAll('.langbtn').forEach(function(b){
      b.setAttribute('aria-pressed', b.dataset.setlang === lang ? 'true' : 'false');
    });
    try { localStorage.setItem('so101_manual_lang', lang); } catch(e){}
  }
  var saved = 'en';
  try { saved = localStorage.getItem('so101_manual_lang') || 'en'; } catch(e){}
  apply(saved);
  document.querySelectorAll('.langbtn').forEach(function(b){
    b.addEventListener('click', function(){ apply(b.dataset.setlang); });
  });
})();
"""


def page() -> str:
    nav = "\n".join(
        f'  <a class="navpill" href="#{a}"><span class="n">{n}</span>{sp(en, ja)}</a>' for a, n, en, ja in NAV)
    hero = (
        '<div class="hero">\n'
        f'<h1>{sp("SO-101 Control Station — User Manual", "SO-101 Control Station — ユーザーマニュアル")}</h1>\n' +
        p("A step-by-step guide for two robots — the Feetech SO-101 / SO-ARM100 and the Damiao reBot B601-DM — from an unopened kit to a calibrated, teleoperated arm, with a screenshot of every screen you will meet.",
          "2種類のロボット（Feetech SO-101 / SO-ARM100 と Damiao reBot B601-DM）を、開封直後から「キャリブレーション済みで遠隔操作できる」状態にするまでの手順書です。実際に表示される各画面のスクリーンショット付き。", "lede") +
        '<div class="meta"><span class="chip">' + f"v{VERSION}" + '</span>'
        '<span class="chip">Setup → Calibration → Drive</span>'
        '<span class="chip" data-i="en">2 robots, 1 window</span><span class="chip" data-i="ja">2台のロボット、1つのウィンドウ</span>'
        '</div></div>\n')
    foot = (
        '<div class="foot">' +
        p(f'SO-101 Control Station — <a href="{REPO}">github.com/HaikalBaiqunni/so101-control-station</a>. MIT licensed. Made by Haikal Baiqunni.',
          f'SO-101 Control Station — <a href="{REPO}">github.com/HaikalBaiqunni/so101-control-station</a>。MITライセンス。作者：Haikal Baiqunni。') +
        '</div>\n')
    return (
        '<!doctype html>\n<html lang="en" data-lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>SO-101 Control Station — User Manual</title>\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">\n'
        f'<style>\n{CSS}\n</style>\n</head>\n<body>\n'
        '<div class="topbar">\n  <div class="brand"><span class="name">SO-101 Control Station</span>'
        f'<span class="tag" data-i="en">User Manual</span><span class="tag" data-i="ja">ユーザーマニュアル</span></div>\n'
        '  <div class="langtoggle" role="group" aria-label="Language">'
        '<button class="langbtn" data-setlang="en" aria-pressed="true">EN</button>'
        '<button class="langbtn" data-setlang="ja" aria-pressed="false">日本語</button></div>\n</div>\n'
        f'<nav class="toc" aria-label="Sections">\n{nav}\n</nav>\n<main>\n{hero}\n{content()}\n{foot}</main>\n'
        f'<script>{SCRIPT}</script>\n</body>\n</html>\n')


def data_uri(image) -> str:
    buf = io.BytesIO()
    image.save(buf, "WEBP", quality=82, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def main() -> None:
    import manual_shots

    cache = ROOT / "tools" / "_shots"
    if "--cached" in sys.argv and cache.is_dir():   # reuse the last capture: text-only edits rebuild in seconds
        from PIL import Image
        shots = {f.stem: Image.open(f).convert("RGB") for f in cache.glob("*.png")}
    else:
        shots = manual_shots.capture()
    for name, image in shots.items():
        IMAGES[name] = data_uri(image)
    html = page()
    out = ROOT / "docs" / "MANUAL.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html) / 1e6:.2f} MB, {len(IMAGES)} images)")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
