from nmigen import *
from nmigen.build import *
from .serdes import K, D, Ctrl, PCIeSERDESInterface
from .layouts import ts_layout

# TODO: When TS data changes during TS sending, the sent TS changes. For example when it changes from TS1 to TS2, itll send ...D10.2 D10.2 D5.2 D5.2 which is kinda suboptimal. TS should be buffered.
class PCIePhyTX(Elaboratable):
    """
    PCIe Transmitter for 1:2 gearing

    Parameters
    ----------
    lane : PCIeSERDESInterface
        PCIe lane
    ts : Record(ts_layout)
        Data to send
    """
    def __init__(self, lane : PCIeSERDESInterface):
        assert lane.ratio == 2
        self.lane = lane
        self.ts = Record(ts_layout)
        self.idle = Signal()
        self.sending_ts = Signal()

    def elaborate(self, platform: Platform) -> Module:
        m = Module()

        lane = self.lane
        ts = self.ts # ts to transmit
        self.start_send_ts = Signal()
        self.idle = Signal()
        self.eidle = Signal(2)
        symbol1 = lane.tx_symbol[0: 9]
        symbol2 = lane.tx_symbol[9:18]

        skp_counter = Signal(range(769))
        skp_accumulator = Signal(4)
        
        # Increase SKP accumulator once counter reaches 650 (SKP between 1180 and 1538 symbol times, here 1300)
        m.d.rx += skp_counter.eq(skp_counter + 1)
        with m.If(skp_counter == 650):
            m.d.rx += skp_counter.eq(0)
            with m.If(skp_accumulator < 15):
                m.d.rx += skp_accumulator.eq(skp_accumulator + 1)

        # Structure of a TS:
        # COM Link Lane n_FTS Rate Ctrl ID ID ID ID ID ID ID ID ID ID
        with m.FSM(domain="rx"):

            with m.State("IDLE"):

                m.d.rx += self.sending_ts.eq(0)

                # Send SKP ordered sets when the accumulator is above 0
                with m.If(skp_accumulator > 0):
                    m.d.rx += [
                        symbol1.eq(Ctrl.COM),
                        symbol2.eq(Ctrl.SKP),
                        skp_accumulator.eq(skp_accumulator - 1),
                        self.sending_ts.eq(1),
                    ]
                    m.next = "SKP-ORDERED-SET"

                with m.Elif(ts.valid):
                    m.d.rx += lane.tx_e_idle.eq(0b0)
                    m.next = "TSn-LANE-FTS"
                    m.d.rx += [
                        symbol1.eq(Ctrl.COM),
                        #lane.tx_set_disp[0].eq(1), If the FSM is in the TX domain it works with this, in the RX domain it should be commented out, not sure why that is
                        #lane.tx_disp[0].eq(0),
                        self.start_send_ts.eq(1)
                    ]

                    # Send PAD symbols if the link is invalid, otherwise send the link number.
                    with m.If(ts.link.valid):
                        m.d.rx += symbol2.eq(Cat(ts.link.number, Signal())) # Hopefully the right order?
                    with m.Else():
                        m.d.rx += symbol2.eq(Ctrl.PAD)

                # Transmit idle data
                with m.Elif(self.idle):
                    m.d.rx += symbol1.eq(0)#(Ctrl.IDL)
                    m.d.rx += symbol2.eq(0)#(Ctrl.IDL)

                # Otherwise go to electrical idle, if told so
                with m.Else():
                    m.d.rx += lane.tx_e_idle.eq(self.eidle)
                #with m.Else():
                #    m.d.rx += lane.tx_e_idle.eq(0b11)


            with m.State("SKP-ORDERED-SET"):
                m.d.rx += [
                    symbol1.eq(Ctrl.SKP),
                    symbol2.eq(Ctrl.SKP),
                ]
                m.next = "IDLE"


            with m.State("TSn-LANE-FTS"):
                m.d.rx += lane.tx_set_disp[0].eq(0)
                m.d.rx += self.start_send_ts.eq(0)

                # Send PAD symbols if the lane is invalid, otherwise send the lane number.
                with m.If(ts.lane.valid):
                    m.d.rx += symbol1.eq(Cat(ts.lane.number, Signal()))
                with m.Else():
                    m.d.rx += symbol1.eq(Ctrl.PAD)
                m.d.rx += symbol2.eq(Cat(ts.n_fts, Signal()))
                m.next = "TSn-DATA"


            with m.State("TSn-DATA"):
                m.d.rx += symbol1.eq(Cat(ts.rate, Signal()))
                m.d.rx += symbol2.eq(Cat(ts.ctrl, Signal(4)))
                m.next = "TSn-ID0"


            for i in range(5):
                with m.State("TSn-ID%d" % i):
                    m.next = "IDLE" if i == 4 else "TSn-ID%d" % (i + 1)
                    with m.If(ts.ts_id == 0):
                        m.d.rx += [
                            symbol1.eq(D(10,2)),
                            symbol2.eq(D(10,2))
                        ]
                    with m.Else():
                        m.d.rx += [
                            symbol1.eq(D(5,2)),
                            symbol2.eq(D(5,2))
                        ]
        return m