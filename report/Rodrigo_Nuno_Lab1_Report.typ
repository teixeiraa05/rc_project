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
#show heading: set block(above: 1em, below: 1em)
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
      up202404802\@up.pt
    ],
    [
      *Nuno João Pires Carvalho* \
      up202406771\@up.pt
    ],
  )
  #v(1mm)
  *Practical class:* 3LEIC02 #h(1fr) 8 November 2026
]

#v(5mm)

= Summary
This report measures the efficiency of our Stop-and-Wait data-link protocol and
compares it with a theoretical full-transfer model. Of #results.total transfers
of a 16,384-byte file, #results.passed delivered the file correctly. In the
reference configuration (9,600 bit/s, 512-byte frames), measured efficiency was
#results.baseline_eff against #results.baseline_theory predicted. Increasing the
frame size from 64 to 1,000 bytes raised efficiency from #results.small_eff to
#results.large_eff, while capacities from 1,800 to 38,400 bit/s kept it between
#results.high_eff and #results.low_eff. A 100 ms propagation delay lowered it to
#results.delay_eff, and a 20% target Frame Error Rate (FER) to #results.fer_eff.
Without errors, the model stayed within 1.7 percentage points of every
measurement; at 20% FER, it exceeded the measurement by 3.8 points.

#if not results.current [
  #text(size: 10pt)[*Measurement update pending:* The updated plan below requires
  #results.planned_total trials. Saved graphs and numerical results still describe
  the earlier #results.total trials; run the updated scripts and rebuild this PDF.]
]


= Introduction
Our program transfers files over an RS-232 serial link using two independent
layers. The application layer splits the file into DATA packets, sends them
between START and END control packets, and rebuilds the file at the receiver.
It passes each packet to the link layer, which carries it in an information (I)
frame without interpreting the application contents. The link layer handles
framing, byte stuffing and error detection, then delivers each accepted packet
to the receiving application.

The link layer uses Stop-and-Wait Automatic Repeat Request (ARQ) to recover
from errors. After each I frame, the transmitter waits for a reply. Replies
confirm reception (RR) or request retransmission (REJ); a timeout also
triggers retransmission. Sequence numbers alternate between 0 and 1 so the
receiver can acknowledge duplicates without passing them to the application twice.
Waiting for replies reduces the time available to send file data. 

We vary capacity $C$, frame size $L$, propagation delay $T_"prop"$ and FER,
one at a time, and compare the measured full-transfer efficiency $S$.

= Methodology
Each of the 19 unique configurations in @sweeps is tested in 20 repeated file
transfers: #results.planned_total trials, all using the same 16,384-byte file.
Results for shared configurations are reused across figures and tables.
Trials were run concurrently using threads.

The evaluator connects transmitter and receiver through the virtual cable and
starts the receiver before the transmitter. As stated before, the same file is used throughout
so that differences reflect transmission conditions rather than file contents.

The cable sets the link capacity, delay and errors, while both endpoint serial
ports keep a baud rate of 9,600 bit/s.
The serial port uses ten bits per byte (8N1).
The protocol uses a four-second timeout and three retries.

For I frames carrying DATA packets, $L=P+6$, where $P$ is the complete application
DATA packet size. Table values are nominal sizes before stuffing.

#figure(
  table(
    columns: (1.2fr, 2fr, 2fr),
    align: left,
    inset: 7pt,
    stroke: 0.4pt,
    fill: (_, row) => if row == 0 { rgb("#eeeeee") },
    table.header([*Parameter*], [*Values*], [*Fixed conditions*]),
    [Capacity $C$], [1,800; 2,400; 4,800; 9,600; 19,200; 38,400 bit/s],
    [$L=512$ bytes; $p_e=0$; $T_"prop"=0$ ms],
    [Frame size $L$], [64; 128; 256; 512; 1,000 bytes],
    [$C$ = 9,600 bit/s; $p_e=0$; $T_"prop"=0$ ms],
    [Propagation delay $T_"prop"$], [0; 10; 25; 50; 100 ms],
    [$C$ = 9,600 bit/s; $L=512$ bytes; $p_e=0$],
    [Frame Error Rate $p_e$], [0%; 2.5%; 5%; 10%; 15%; 20%],
    [$C$ = 9,600 bit/s; $L=512$ bytes; $T_"prop"=0$ ms],
  ),
  kind: table,
  caption: [Test profiles.],
) <sweeps>

The cable accepts Bit Error Rate (BER). We convert target FER using
$"BER" = 1-(1-p_e)^(1/(8L))$. For this we use the measured length of full-sized I frames after byte stuffing, obtained from error-free transfers.
Noise affects both directions,
including replies; shorter I frames consequently have lower error probabilities.
Added propagation delay also applies in each direction, separately from transmission time.

Throughput is $R=(8M)/T$ and efficiency is $S=R/C$, where $M$ is the file size
in bytes and $T$ the transfer duration in seconds. $T$ runs from transmitter
launch until both endpoints finish, covering opening, START, all I frames
carrying DATA packets, END, retransmissions and closing. A trial succeeds only
when both programs finish successfully and the received file matches the
original byte for byte.

For each configuration, $T$ is the mean duration of its successful trials, and
error bars show approximate standard errors of that mean. Completion counts
include every trial, so the efficiency plots describe successful transfers and
Table 2 reports reliability.

The class theoretical model counts successful frame-transmission time. With $a=T_"prop"/T_f$,
$ S_"frame"=(1-p_e)/(1+2a) = ((1-p_e)T_f)/(T_f+2T_"prop") = T_f/E[T_"del"]. $
Thus the expected delivery time of one frame is
$ E[T_"del"] = T_"cycle"/(1-p_e), quad T_"cycle" = T_f + 2T_"prop". $
With 8N1 and a five-byte reply, the $j$-th I frame carrying a DATA packet
($j = 1, ..., N$), of measured length $L_j$, has
$ T_("cycle",j) = (10 L_j)/C + T_"reply" + 2T_"prop", quad T_"reply" = 50/C, quad
  E[T_("del",j)] = T_("cycle",j)/(1-p_e). $
Summing the delivery times of all I frames carrying DATA packets, START, END and
both handshakes gives the expected full-transfer time:
$ E[T_"transfer"] = E[T_"open"] + E[T_"START"] + sum_j E[T_("del",j)] + E[T_"END"] + E[T_"close"]. $
#block(width: 100%, above: 0.75em, below: 0.75em)[
  #align(center, box(stroke: 0.6pt, inset: 8pt)[
    $display(S_"theory" = (8M)/(C E[T_"transfer"]))$
  ])
]
The model uses recorded stuffed lengths, including the shorter final I frame
carrying a DATA packet, and the measured propagation delay. It assumes clean
handshakes, independent errors, equal-duration retries of each frame and
unlimited attempts, and applies the target FER uniformly to I frames. It counts
transmission and propagation time only; processing time, reply corruption and
timeout waits fall outside it.

= Results
#grid(
  columns: (1fr, 1fr), gutter: 4mm,
  [#figure(image("assets/efficiency-capacity.svg", width: 100%),
    caption: [Effect of capacity $C$.]) <capacity>],
  [#figure(image("assets/efficiency-frame-size.svg", width: 100%),
    caption: [Effect of frame size $L$.]) <frame-size>],
  [#figure(image("assets/efficiency-a.svg", width: 100%),
    caption: [Effect of delay $T_"prop"$.]) <delay>],
  [#figure(image("assets/efficiency-fer.svg", width: 100%),
    caption: [Effect of FER.]) <fer>],
)

Figures 1–4 show measured means with standard-error bars and theory as dashed
lines; @sweeps lists the settings.

*Capacity.* Between 1,800 and 38,400 bit/s, throughput rose from
#results.low_rate to #results.high_rate kbit/s and efficiency moved from
#results.low_eff to #results.high_eff (@capacity). Faster transmission shortens
file-data and overhead transmission times proportionally, so efficiency stays
nearly constant. The model predicts #results.high_theory at every capacity; the
1.7-point gap at 38,400 bit/s is consistent with fixed processing time taking a
larger share of a 4.56 s transfer.

*Frame size.* Efficiency rose from #results.small_eff at 64 bytes to
#results.large_eff at 1,000 bytes (@frame-size), against #results.small_theory and
#results.large_theory predicted. Larger frames carry more file bytes per header
and reply, reducing the number of I frames from 298 to 17.
Gains diminish as this per-frame overhead shrinks, while opening, START, END and
closing keep a fixed cost.

*Propagation delay.* At 100 ms, measured efficiency is #results.delay_eff against
#results.delay_theory predicted (@delay). Each exchange that waits for a reply
adds one round trip ($2T_"prop"$ = 0.2 s) during which the link is idle. Adding
100 ms lengthened the transfer by #results.full_delta s, matching the model's
7.50 s: the 33 I frames carrying DATA packets account for #results.data_delta s,
and opening, START, END and closing (4.5 round trips) for #results.outside_delta s.
A model counting only DATA packets would therefore underestimate the delay cost by 12% (predicting 6.6 s).

*Errors.* At 20% target FER, measured efficiency is #results.fer_eff, 3.8
percentage points below the #results.fer_theory predicted (@fer). A frame with a valid header and
corrupt data receives REJ and is retransmitted immediately, whereas a corrupted
header or reply leaves the transmitter waiting for the four-second timeout, a
wait outside the model. Shorter I frames (START, END and the final DATA frame)
have lower error probabilities than the target, which favours the measurement.
Two of the 20 transfers at 20% FER failed after exhausting the three retries.

#include "assets/results-summary.typ"

= Conclusions
Frame size, delay and errors set the protocol's efficiency, while capacity kept
it between #results.high_eff and #results.low_eff. Raising $L$ from 64 to 1,000 bytes increased $S$ from
#results.small_eff to #results.large_eff by spreading each header and reply over
more file bytes; 100 ms of delay and 20% FER reduced it to #results.delay_eff and
#results.fer_eff by adding idle round trips and retransmissions.

The model is valid for error-free transfers, staying within 1.7 percentage points
of all 14 such configurations; the gap grows where fixed processing time weighs
most, at high capacity and small frames. At 20% FER, it overestimated $S$ by 3.8
points, consistent with timeouts after corrupted headers or replies. Unlike the
model's unlimited attempts, four of the 100 noisy transfers failed after three
retries, so efficiency must be read with completion rate.

*AI Disclaimer:* AI assisted in preparing the evaluation scripts and refining the report wording and formatting.
