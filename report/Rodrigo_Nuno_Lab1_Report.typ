// Compile from the repository root:
// typst compile report/Rodrigo_Nuno_Lab1_Report.typ report/Rodrigo_Nuno_Lab1_Report.pdf
// Logo from João Correia Lopes et al., Template PI L.EIC (CC BY 4.0):
// https://pt.overleaf.com/latex/templates/template-pi-leic/dygyvxbnrjcv
// The logo is extracted from that template; measurements are in evaluation/results.csv.
#import "assets/results-meta.typ": results

#set document(
  title: "Lab 1 — Characterisation of Protocol Efficiency",
  author: ("Rodrigo Fernandes Teixeira", "Nuno João Pires Carvalho"),
)
#set page(paper: "a4", margin: (x: 15mm, y: 16mm), numbering: "1", number-align: center)
#set text(font: "Liberation Serif", size: 11pt, lang: "en")
#set par(justify: true, leading: 0.5em, spacing: 1.15em)
#set heading(numbering: none)
#show heading: set text(size: 11pt, weight: "bold")
#show heading: set block(above: 1.1em, below: 0.9em)
#set figure(gap: 8pt)
#show figure: set block(above: 18pt, below: 18pt)
#show math.equation.where(block: true): set block(above: 0.75em, below: 0.75em)
#show figure.caption: set par(justify: false)
#show figure.where(kind: table): set figure.caption(position: top)
#show table.cell: set par(justify: false)

#grid(
  columns: (30mm, 1fr),
  column-gutter: 5mm,
  align: (left, left),
  image("assets/uporto-feup.png", width: 30mm),
  [
    #text(size: 10pt)[Faculty of Engineering of the University of Porto] \
    #text(size: 14pt, weight: "bold")[Characterisation of Protocol Efficiency] \
    #text(size: 10pt)[Computer Networks — Lab 1 Report]
  ],
)
#v(2mm)
#text(size: 10pt)[
  #grid(
    columns: (1fr, 1fr),
    column-gutter: 5mm,
    [
      *Rodrigo Fernandes Teixeira* \
      #link("mailto:up202404802@up.pt")[up202404802\@up.pt]
    ],
    [
      *Nuno João Pires Carvalho* \
      #link("mailto:up202406771@up.pt")[up202406771\@up.pt]
    ],
  )
  #v(1mm)
  *Practical class:* 3LEIC02 #h(1fr) 27 October 2026
]

#v(5mm)

= Summary
This study characterises a Stop-and-Wait protocol through #results.total file
transfers over a virtual serial link. Of these, #results.passed delivered the
4096-byte file correctly. With zero FER and no added propagation delay,
useful-file efficiency was #results.baseline_eff at 9600 bit/s with 512-byte file
payloads. At 100 ms added one-way delay, efficiency was #results.delay_eff.
The results are compared with the ideal Stop-and-Wait model.

#if not results.current [
  #text(size: 10pt)[*Measurement update pending:* The updated plan below requires
  #results.planned_total trials. Saved graphs and numerical results still describe
  the earlier #results.total trials; run the updated scripts and rebuild this PDF.]
]


= Introduction
Stop-and-Wait provides reliable communication by acknowledging each frame before
sending the next. The implemented serial protocol combines framing, byte
stuffing, checksums, alternating sequence numbers and retransmissions. Its
performance depends on the time spent carrying useful data relative to framing,
acknowledgements and recovery. This study examines the effects of link capacity,
frame size, propagation delay and frame error rate on useful throughput.

= Methodology
The updated evaluation has 30 configurations, each transferring the same
4096-byte file. Each of the six FER settings runs 20 times; the remaining 24
configurations run five times, for #results.planned_total transfers in total.
Shared configurations are counted once; the reference and 5% FER baseline
therefore retain their 20 trials when used in other comparisons.
The virtual cable controls capacity, added one-way propagation delay and bit
errors; endpoint serial settings remain at 9600 bit/s. The link-layer payload
limit stays at 1000 bytes. The size sweep uses application DATA packets of
64, 128, 256, 512 and 1000 bytes, including the three-byte header, giving file
chunks of 61, 125, 253, 509 and 997 bytes. The implementation uses a four-second
timeout and three retransmissions. The figures use the profiles in @sweeps;
capacity and packet-size comparisons use error-free trials with no added delay.

#figure(
  table(
    columns: (1.2fr, 2fr, 2fr),
    align: left,
    inset: 7pt,
    stroke: 0.4pt,
    fill: (_, row) => if row == 0 { rgb("#eeeeee") },
    table.header([*Parameter*], [*Values*], [*Fixed conditions*]),
    [Capacity $C$], [1.8, 2.4, 4.8, 9.6, 19.2, 38.4 kbit/s],
    [$P=512$ bytes; $p_e=0$; $T_"prop"=0$ ms],
    [DATA packet size], [64, 128, 256, 512, 1000 bytes],
    [$C=9.6$ kbit/s; $p_e=0$; $T_"prop"=0$ ms],
    [Propagation delay $T_"prop"$], [0, 10, 25, 50, 100 ms],
    [$C=9.6$ kbit/s; $P=512$ bytes; $p_e=0$],
    [Frame Error Rate ($p_e$)], [0%, 2.5%, 5%, 10%, 15%, 20%],
    [$C=9.6$ kbit/s; $P=512$ bytes; $T_"prop"=0$ ms],
  ),
  kind: table,
  caption: [Updated test profiles. $P$ denotes file payload. DATA packet sizes
    include the three-byte application header; link framing and stuffing add wire overhead.],
) <sweeps>

A DATA packet holds at most 1000 bytes. Its three-byte header leaves up to
997 file bytes. The cable accepts a bit error rate (BER). To target FER $p_e$,
we calculated $"BER"=1-(1-p_e)^(1/(8L))$ for each payload size, where $L$ is
the mean full stuffed DATA-frame length in bytes measured in clean trials.
This BER was applied in both directions, including acknowledgements. Stuffing
variations and shorter frames change the actual FER.
In the saved dataset, #results.passed of #results.total transfers passed
byte-for-byte verification.

For file size $B$ and full-transfer duration $T$, useful throughput was $R=8B/T$
and measured efficiency was $S=R/C$. Duration included connection establishment,
START/END packets, DATA, retransmissions and closure. Group throughput was the
total useful bits divided by total duration across successful trials. Error bars
represent approximate standard errors across successful repetitions.

#pagebreak()

= Theoretical Efficiency
With stuffed DATA-frame length $L$ in bytes and capacity $C$, transmission time
and normalised propagation delay are
$ T_f=10L/C, quad a=T_"prop"/T_f. $
For an error-free link,
$ S_0=1/(1+2a). $
The theoretical Stop-and-Wait efficiency is
$ S=(1-p_e)/(1+2a), $
where $p_e$ is the target FER and $a$ is the ratio of one-way propagation time
to DATA-frame transmission time. BER is calculated from the target FER only to
configure the cable for each payload size.

The plotted prediction applies this Stop-and-Wait reasoning to useful-file
throughput using the complete DATA/reply cycle. DATA transmission time is
calculated from the recorded stuffed frame lengths and link capacity, with ten
serial bits per byte. Each attempt also includes transmission of the five-byte
reply and twice the one-way propagation time, accounting for the outward DATA
and returning reply. The configured propagation time is included even when small;
no additional baseline delay is assumed when the configured value is zero.
The prediction counts only useful file bits and accounts for the number of DATA
frames, including the short final frame. It uses the target FER for repeated
attempts and does not use measured transfer durations. Setup/closure, START/END
packets, processing, ACK corruption and four-second timeout waits remain outside
this approximation.

= Results
#grid(
  columns: (1fr, 1fr), gutter: 4mm,
  [#figure(image("assets/efficiency-capacity.svg", width: 100%),
    caption: [Effect of capacity $C$.]) <capacity>],
  [#figure(image("assets/efficiency-frame-size.svg", width: 100%),
    caption: [#if results.current [Effect of DATA packet size.] else [Earlier size sweep; updated packet sizes pending.]]) <payload>],
  [#figure(image("assets/efficiency-a.svg", width: 100%),
    caption: [Effect of delay $T_"prop"$.]) <delay>],
  [#figure(image("assets/efficiency-fer.svg", width: 100%),
    caption: [Effect of FER ($p_e$).]) <fer>],
)

Figures 1–4 compare measured efficiency, with approximate standard errors,
against the ideal Stop-and-Wait reference (dashed). Fixed conditions are in @sweeps.

*Capacity: the reference counts different overheads.* Useful throughput rose
from #results.low_rate to #results.high_rate kbit/s between 1800 and 38400 bit/s,
while efficiency changed from #results.low_eff to #results.high_eff (@capacity).
With no added delay and zero FER, DATA and reply transmission times scale
inversely with capacity, giving constant predicted efficiency. The prediction
includes DATA framing and replies; setup/closing packets and processing consume
additional time. These omitted costs explain why the
baseline reaches #results.baseline_eff rather than #results.baseline_theory,
a gap of #results.baseline_gap percentage points. Serialization costs
scale with capacity, so they lower efficiency without a strong capacity trend.

*Packet size: overhead is a larger fraction of short packets.* DATA packet sizes
64, 128, 256, 512 and 1000 bytes include the three-byte application header.
Sending the 4096-byte file requires 68 DATA packets at the smallest setting and
five at the largest. More headers and ACKs are therefore required per useful
file byte for smaller packets. The plotted prediction includes these costs
through stuffed DATA-frame lengths, frame count and reply transmission. Predicted
and measured efficiency rise as useful data occupies a larger fraction of transmission time. The size
axis shows the configured packet size, not an averaged wire-frame length.

*Propagation delay: the measured time contains extra round trips.* At 100 ms
added one-way delay, theory predicts #results.delay_theory whereas measured
efficiency is #results.delay_eff (@delay). The formula models DATA cycles and
includes ACK serialization but omits setup/control/closing exchanges. Between zero and
100 ms, the DATA interval grew by #results.data_delta s, while full-transfer
time grew by #results.full_delta s; #results.outside_delta s of the increase was
outside DATA. These surrounding exchanges also incur propagation delay, which
explains why the reference underestimates total waiting time.

*Errors: timeout recovery differs from an ordinary repeated attempt.* At a target
FER of 20%, measured efficiency is #results.fer_eff against #results.fer_theory
predicted (@fer). The simple factor $1-p_e$ assumes independent, equal-duration
attempts at the target FER. A valid REJ can trigger a prompt retry, but a missing or corrupted
reply can require the four-second timeout. The cable also corrupts ACKs, and
short frames have different error probabilities from the reference frame.
The implementation permits three retransmissions, rather than unlimited retries.
These differences and random recovery paths can produce a gap even with 20
repetitions per FER setting.

Software handling and userspace timing add further costs absent from the ideal
reference. The measurements establish the total gap, without isolating the
contribution of each omitted cost.

#include "assets/results-summary.typ"

= Conclusions
Of the #results.total saved transfers, #results.passed delivered the original
file correctly. Larger payloads improved
useful efficiency by reducing overhead per file byte. Higher capacity increased
throughput with nearly constant efficiency, while propagation delay and frame
errors reduced both. The simple Stop-and-Wait reference describes broad utilisation trends;
remaining differences reflect protocol overhead, connection management,
processing and timeout recovery.

*AI Disclaimer:* AI assisted in preparing the evaluation scripts and report.
