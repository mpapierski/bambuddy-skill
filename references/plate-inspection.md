# Inspect a finished print

Use this workflow when the user asks what is on a plate or how a completed print
looks. A visual inspection does not itself authorize a print, plate-clearance
acknowledgment, homing, or printer motion.

1. Discover the intended printer and inspect its current status and recent job.
   Report whether the job actually finished; a completed status does not prove
   that the printed object is present, intact, or adherent.
2. Capture a fresh frame with `camera-snapshot` as described in
   [CLI usage](usage.md), and actually inspect the image. Describe visible
   objects and defects, and show the frame when useful. Distinguish observations
   from guesses about identity or print quality.
3. If any object is visibly on the plate, report it occupied even if the object
   or plate is partly cropped. If the whole relevant surface is clear and visible,
   report it appears empty. If darkness, cropping, or an obstruction prevents the
   requested judgment, say what cannot be determined. An `awaiting_plate_clear`
   flag is queue state, not visual evidence of physical contents.

Bambuddy's `get_plate_detection_status` and `check_plate_empty` can provide
additional evidence where applicable. Check calibration, the selected camera and
plate type, lighting, and the region covered; a detector confidence value alone
does not prove that a cropped or obscured plate is empty. If its result conflicts
with the image, report the disagreement rather than hiding it.

## Automatically acknowledge a clear plate

When the user explicitly requests a plate-clearance acknowledgment, first check a
fresh image, printer state and queued jobs. Only acknowledge clearance when the
relevant plate is fully visible and confidently empty, the printer is otherwise
ready, and any resulting queue dispatch is within the user's authorization. A
print, debris, an obscured or poor-quality image, a conflicting detection result,
or an unresolved printer fault means the condition is not established: leave the
plate awaiting clearance and report what needs attention.

Use `describe clear_plate`, review the `--dry-run`, then call `clear_plate` with
`--write` for the discovered printer ID. Verify the resulting printer and queue
state. The endpoint only updates Bambuddy's flag; it does not remove objects and
may cause the scheduler to start the next queued print. An inspection request
alone does not authorize this acknowledgment or a subsequent print.

## When a better view requires movement

Do not move the bed simply to perform an inspection when the existing view
answers the question. If the requested observation remains uncertain and a better
angle would help, explain what is missing and ask the user about repositioning
unless a suitable move was already authorized. A supervised manual move from the
printer's controls can be used when the necessary clearances cannot be verified
remotely; then capture and inspect another fresh frame.

Before proposing a numerical move, inspect the actual last completed job's
corresponding plate G-code, if available. Identify its final intended Z position,
respecting absolute/relative modes, resets and conditional moves; use the
finished object's height and subsequent known motion only as evidence. Explain
the calculation and its assumptions. A command in the file is not a live position
reading, and the printed model may not be the tallest or only object on the plate.
Account for the current position, overhead and under-bed obstructions, hardware
limits and an appropriate margin. For example, intended end Z minus model height
is only a nominal nozzle-reference gap, not a verified physical clearance. If
the actual clearances are established, propose the smallest useful move within
those limits and explain the calculation. If they are unknown, do not present an
estimated travel as a verified safe value.

Inspect `bed_jog` before any authorized remote move. Its documented Z value is a
nozzle-to-bed gap change: positive opens the gap, negative closes it; on an X1C a
negative value raises the bed. The endpoint describes its relative G-code moves
as unguarded by soft limits, and the available status does not provide a current
axis position. Do not home an occupied or uncertain plate to establish position.
If adequate clearance cannot be established, leave the printer in place and ask
for a supervised adjustment or the missing measurements. Never treat a successful
jog request as proof of where the bed actually ended up.
