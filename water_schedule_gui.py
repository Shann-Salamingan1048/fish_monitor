"""Tk schedule editor; database and serial work run off the Tk event loop."""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import config
import water_schedule
from gui_snapshot import SnapshotWorker


class WaterScheduleWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title('AquaPi • Water-change schedule')
        self.geometry(f'{min(850,self.winfo_screenwidth()-40)}x{min(650,self.winfo_screenheight()-60)}')
        self.minsize(580,400)
        self.transient(parent)
        self.editing=None;self.rows={};self.busy=False;self.signature=None
        self.results=queue.Queue();self.after_id=None
        panel=ttk.Frame(self,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Water-change schedule',font=('Arial',16,'bold')).pack(anchor='w')
        ttk.Label(panel,text='Timed drain, then refill to Normal. Ammonia is not measured.',wraplength=550).pack(anchor='w',pady=6)
        self.summary=tk.StringVar();ttk.Label(panel,textvariable=self.summary,wraplength=550).pack(anchor='w',pady=6)
        form=ttk.Frame(panel);form.pack(fill='x')
        self.day=tk.StringVar(value=config.FEEDING_DAY_NAMES[0]);self.at=tk.StringVar()
        self.drain=tk.StringVar();self.refill=tk.StringVar()
        self.controls=[]
        for i,(label,var) in enumerate([('Day',self.day),('Time (HH:MM)',self.at),('Drain duration (seconds)',self.drain),('Refill timeout (seconds)',self.refill)]):
            ttk.Label(form,text=label).grid(row=i,column=0,sticky='w',pady=4)
            field=ttk.Combobox(form,textvariable=var,values=config.FEEDING_DAY_NAMES,state='readonly',width=28) if i==0 else ttk.Entry(form,textvariable=var,width=18)
            field.grid(row=i,column=1,sticky='ew',padx=8,pady=4);self.controls.append(field)
        form.columnconfigure(1,weight=1)
        ttk.Label(panel,text='Choose durations after testing pump flow. Drain stops at the Low float or time limit; refill stops at Normal. Busy or missed runs are skipped.',wraplength=550).pack(anchor='w',pady=8)
        actions=ttk.Frame(panel);actions.pack(fill='x',pady=4)
        self.save_button=ttk.Button(actions,text='Add schedule',command=self.save);self.save_button.pack(side='left',padx=3);self.controls.append(self.save_button)
        self.cancel_button=ttk.Button(actions,text='Cancel edit',command=self.reset);self.cancel_button.pack(side='left',padx=3);self.controls.append(self.cancel_button)
        area=ttk.Frame(panel);area.pack(fill='both',expand=True,pady=8)
        columns=('day','time','drain','refill','state','last')
        self.tree=ttk.Treeview(area,columns=columns,show='headings',height=6)
        for key,title,width in zip(columns,('Day','Time','Drain (s)','Refill max (s)','State','Last run'),(180,65,75,100,65,95)):
            self.tree.heading(key,text=title);self.tree.column(key,width=width,minwidth=45)
        vbar=ttk.Scrollbar(area,orient='vertical',command=self.tree.yview);vbar.pack(side='right',fill='y')
        hbar=ttk.Scrollbar(area,orient='horizontal',command=self.tree.xview);hbar.pack(side='bottom',fill='x')
        self.tree.configure(yscrollcommand=vbar.set,xscrollcommand=hbar.set);self.tree.pack(fill='both',expand=True)
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        for title,callback in [('Edit selected',self.edit),('Pause / Enable',self.toggle),('Delete',self.delete),('Stop water change',self.stop)]:
            button=ttk.Button(buttons,text=title,command=callback);button.pack(side='left',padx=3,pady=5);self.controls.append(button)
        self.message=tk.StringVar();ttk.Label(panel,textvariable=self.message,wraplength=550).pack(anchor='w',pady=5)
        self.worker=SnapshotWorker(water_schedule.response)
        self.protocol('WM_DELETE_WINDOW',self.close);self.bind('<Escape>',lambda _:self.close())
        self.poll()

    def selected(self):
        selection=self.tree.selection()
        if not selection:
            self.message.set('Select a schedule first.');return None
        return self.rows.get(int(selection[0]))

    def reset(self):
        if self.busy:return
        self.editing=None;self.at.set('');self.drain.set('');self.refill.set('');self.save_button.configure(text='Add schedule')

    def edit(self):
        if self.busy:return
        row=self.selected()
        if not row:return
        self.editing=row['id'];self.day.set(row['dayName']);self.at.set(row['time'])
        self.drain.set(str(row['drainSeconds']));self.refill.set(str(row['refillSeconds']));self.save_button.configure(text='Save changes')

    def run(self, callback, text, clear=False):
        if self.busy:return
        self.busy=True
        for widget in self.controls:widget.configure(state='disabled')
        def work():
            try: callback();self.results.put((True,text,clear))
            except Exception as exc:self.results.put((False,str(exc),False))
        threading.Thread(target=work,daemon=True).start()

    def save(self):
        payload=dict(day=config.FEEDING_DAY_NAMES.index(self.day.get()),time=self.at.get(),
                     drainSeconds=self.drain.get(),refillSeconds=self.refill.get())
        target=self.editing
        self.run(lambda:water_schedule.save(payload,target),'Water schedule saved.',True)

    def toggle(self):
        row=self.selected()
        if row:self.run(lambda:water_schedule.save({'enabled':not row['enabled']},row['id']),'Schedule updated.')

    def delete(self):
        row=self.selected()
        if row and messagebox.askyesno('Delete schedule','Delete this water-change schedule?',parent=self):
            self.run(lambda:water_schedule.delete(row['id']),'Schedule deleted.',True)

    def stop(self):
        self.run(water_schedule.stop,'Stop requested. Check the tank before resuming automatic control.')

    def render(self, data):
        job=data['active'] or data['last']
        upcoming=data['nextRun']['dateTime'].replace('T',' ') if data['nextRun'] else 'None'
        text=f"Next: {upcoming} • {data['timezone']}"
        if not data['ready']:text+='\nConnect Mega with water-change firmware.'
        if job:text+=f"\n{'Current' if data['active'] else 'Last'}: {job['status']} • {job.get('reason') or ''}"
        self.summary.set(text)
        signature=repr(data['schedules'])
        if signature==self.signature:return
        self.signature=signature;selected=self.tree.selection();self.rows={r['id']:r for r in data['schedules']}
        self.tree.delete(*self.tree.get_children())
        for row in data['schedules']:
            self.tree.insert('', 'end',iid=str(row['id']),values=(row['dayName'],row['time'],row['drainSeconds'],row['refillSeconds'],'Active' if row['enabled'] else 'Paused',row['lastStatus'] or 'Not run'))
        if selected and int(selected[0]) in self.rows:self.tree.selection_set(selected)

    def poll(self):
        try:
            ok,text,clear=self.results.get_nowait();self.busy=False;self.message.set(text)
            for i,widget in enumerate(self.controls):widget.configure(state='readonly' if i==0 else 'normal')
            if clear:self.reset()
            self.worker.request()
        except queue.Empty:pass
        try:self.render(self.worker.results.get_nowait())
        except queue.Empty:pass
        self.after_id=self.after(100,self.poll)

    def close(self):
        self.worker.close()
        if self.after_id:self.after_cancel(self.after_id)
        self.destroy()
