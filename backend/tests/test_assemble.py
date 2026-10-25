from app.indexer.assemble import FileRec, assemble

CUE = """PERFORMER "Auryn Quartet"
TITLE "Beethoven, String Quartets, op.18 (Disk {d})"
FILE "{name}.flac" WAVE
  TRACK 01 AUDIO
    TITLE "Quartet No.{a} in F major, op.18,{a} - Allegro con brio"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "Adagio affettuoso ed appassionato"
    INDEX 01 05:00:00
  TRACK 03 AUDIO
    TITLE "3. Schero (Allegro)"
    INDEX 01 10:00:00
  TRACK 04 AUDIO
    TITLE "Quartet No.{b} in C minor, op.18,{b} - Allegro ma non tanto"
    INDEX 01 15:00:00
  TRACK 05 AUDIO
    TITLE "{e}"
    INDEX 01 20:00:00
"""


def rec(fid, name, kind, meta):
    return FileRec(fs_id=fid, path=f"/m/Q/{name}", dir="/m/Q", name=name, ext="." + name.rsplit(".", 1)[1],
                   kind=kind, size=1, server_mtime=1, meta=meta)


def test_two_cues_in_one_folder_and_movement_inheritance():
    files = []
    for i, (d, name, a, b) in enumerate([(1, "Auryn A", 1, 2), (2, "Auryn B", 3, 4)]):
        files.append(rec(10 + i, f"{name}.flac", "audio", {"duration": 1500, "format": "FLAC", "tags": {}}))
        files.append(rec(20 + i, f"{name}.cue", "cue", {"cue_text": CUE.format(d=d, name=name, a=a, b=b, e="Ave Maria" if d == 1 else "Agnus Dei, BWV 232")}))
    album = assemble("/m/Q", "/m", files)
    got = [(t["disc_no"], t["track_no"], t["work"], t["movement_no"]) for t in album["tracks"]]
    assert got[:5] == [
        (1, 1, "Quartet No.1 in F major, op.18,1", 1),
        (1, 2, "Quartet No.1 in F major, op.18,1", 2),
        (1, 3, "Quartet No.1 in F major, op.18,1", 3),
        (1, 4, "Quartet No.2 in C minor, op.18,2", None),
        (1, 5, "Ave Maria", None),
    ], got
    assert [t["disc_no"] for t in album["tracks"]] == [1] * 5 + [2] * 5
    assert album["tracks"][0]["composer"] == "Ludwig van Beethoven"
