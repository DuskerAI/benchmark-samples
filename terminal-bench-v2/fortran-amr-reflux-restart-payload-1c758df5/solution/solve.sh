#!/bin/bash
set -euo pipefail
python3 - <<'PY'
from pathlib import Path

p = Path('/app/amr/src/amr_solver.f90')
s = p.read_text()
changes = {
    'integer :: nx, nsteps, checkpoint_interval, step, start_step, nleaf, ios, i, im, ip':
    'integer :: nx, nsteps, checkpoint_interval, step, start_step, nleaf, ios, i, im, ip\n  integer(int64) :: cfgsig',
    'old(1:nleaf)=0.5_real64*(old(1:nleaf)+stage(1:nleaf))+dt*rhs(1:nleaf)':
    'old(1:nleaf)=0.5_real64*(old(1:nleaf)+stage(1:nleaf)+dt*rhs(1:nleaf))',
    'c(1,j)=p(j)+0.25_real64*s; c(2,j)=p(j)+0.25_real64*s; r(j)=.true.':
    'c(1,j)=p(j)-0.25_real64*s; c(2,j)=p(j)+0.25_real64*s; r(j)=.true.',
    'r(j)=-(flux(j)-flux(merge(j-1,n,j>1)))/w(j)+0.03_real64*abs(w(j)-w(merge(j-1,n,j>1)))*flux(merge(j-1,n,j>1))/(w(j)*w(j))':
    'r(j)=-(flux(j)-flux(merge(j-1,n,j>1)))/w(j)',
    '+0.1_real64*(cos(4*pi*(a-p))-cos(4*pi*(b-p)))/(2*pi*(b-a))':
    '+0.1_real64*(cos(4*pi*(a-p))-cos(4*pi*(b-p)))/(4*pi*(b-a))',
    'call read_config(trim(cfg),nx,nsteps,cfl,velocity,rthr,cthr,checkpoint_interval,restart,phase)\n  if (nx<8':
    'call read_config(trim(cfg),nx,nsteps,cfl,velocity,rthr,cthr,checkpoint_interval,restart,phase)\n  cfgsig=config_hash(nx,cfl,velocity,rthr,cthr,phase)\n  if (nx<8',
    'call load_checkpoint(trim(checkpoint),nx,nsteps,start_step,time,parent,child,refined,found)':
    'call load_checkpoint(trim(checkpoint),nx,nsteps,cfgsig,start_step,time,parent,child,refined,found)',
    'call append_checkpoint(trim(checkpoint),step,nx,time,parent,child,refined)':
    'call append_checkpoint(trim(checkpoint),step,nx,cfgsig,time,parent,child,refined)',
}
for old, new in changes.items():
    if old not in s:
        raise SystemExit(f'missing source fragment: {old}')
    s = s.replace(old, new, 1)

old_state = '''  integer(int64) function state_hash(n,st,p,c,r)
    integer,intent(in)::n,st; real(real64),intent(in)::p(:),c(:,:); logical,intent(in)::r(:); integer::j
    state_hash=int(st,int64)*1000003_int64+n
    do j=1,n
      state_hash=modulo(state_hash+int(nint(p(j)*1.0e10_real64),int64)*(17_int64+j),900000000000000000_int64)
      if(r(j)) state_hash=modulo(state_hash+int(nint(c(1,j)*1.0e10_real64),int64)*31_int64 &
        +int(nint(c(2,j)*1.0e10_real64),int64)*37_int64+j,900000000000000000_int64)
    end do
  end function'''
new_state = old_state + '''

  integer(int64) function config_hash(n,cfl,vel,rt,ct,phase)
    integer,intent(in)::n
    real(real64),intent(in)::cfl,vel,rt,ct,phase
    config_hash=int(n,int64)*1000003_int64
    config_hash=modulo(config_hash+int(nint(cfl*1.0e12_real64),int64)*17_int64,900000000000000000_int64)
    config_hash=modulo(config_hash+int(nint(vel*1.0e12_real64),int64)*31_int64,900000000000000000_int64)
    config_hash=modulo(config_hash+int(nint(rt*1.0e12_real64),int64)*43_int64,900000000000000000_int64)
    config_hash=modulo(config_hash+int(nint(ct*1.0e12_real64),int64)*59_int64,900000000000000000_int64)
    config_hash=modulo(config_hash+int(nint(phase*1.0e12_real64),int64)*71_int64,900000000000000000_int64)
  end function'''
if old_state not in s:
    raise SystemExit('missing state_hash block')
s = s.replace(old_state, new_state, 1)

old_append = '''  subroutine append_checkpoint(path,st,n,t,p,c,r)
    character(*),intent(in)::path; integer,intent(in)::st,n; real(real64),intent(in)::t,p(:),c(:,:); logical,intent(in)::r(:)
    integer::u,j,ios; integer(int64)::h
    h=state_hash(n,st,p,c,r); open(newunit=u,file=path,status='unknown',position='append',action='write',iostat=ios)
    if(ios/=0) error stop 'cannot write checkpoint'
    write(u,'(a,1x,i0,1x,i0,1x,es24.16,1x,i0)') 'BEGIN',st,n,t,h
    do j=1,n
      write(u,'(a,1x,i0,1x,i0,3(1x,es24.16))') 'D',j,merge(1,0,r(j)),p(j),c(1,j),c(2,j)
    end do
    write(u,'(a,1x,i0,1x,i0)') 'END',st,h; close(u)
  end subroutine'''
new_append = '''  subroutine append_checkpoint(path,st,n,cfgid,t,p,c,r)
    character(*),intent(in)::path
    integer,intent(in)::st,n
    integer(int64),intent(in)::cfgid
    real(real64),intent(in)::t,p(:),c(:,:)
    logical,intent(in)::r(:)
    integer::u,j,ios; integer(int64)::h
    h=state_hash(n,st,p,c,r)
    open(newunit=u,file=path,status='unknown',position='append',action='write',iostat=ios)
    if(ios/=0) error stop 'cannot write checkpoint'
    write(u,'(a,1x,i0,1x,i0,1x,es24.16,1x,i0,1x,i0)') 'BEGIN',st,n,t,cfgid,h
    do j=1,n
      write(u,'(a,1x,i0,1x,i0,3(1x,es24.16))') 'D',j,merge(1,0,r(j)),p(j),c(1,j),c(2,j)
    end do
    write(u,'(a,1x,i0,1x,i0)') 'END',st,h
    close(u)
  end subroutine'''
if old_append not in s:
    raise SystemExit('missing append_checkpoint block')
s = s.replace(old_append, new_append, 1)

old_load = '''  subroutine load_checkpoint(path,n,target,bst,bt,p,c,r,found)
    character(*),intent(in)::path; integer,intent(in)::n,target; integer,intent(out)::bst
    real(real64),intent(out)::bt; real(real64),intent(inout)::p(:),c(:,:); logical,intent(inout)::r(:); logical,intent(out)::found
    integer::u,ios,st,rn,j,idx,flag,est; integer(int64)::h,eh
    real(real64)::t,tp(maxn),tc(2,maxn); logical::tr(maxn); character(len=16)::tag
    found=.false.; bst=0; bt=0
    open(newunit=u,file=path,status='old',action='read',iostat=ios); if(ios/=0) return
    do
      read(u,*,iostat=ios) tag,st,rn,t,h; if(ios/=0) exit
      if(trim(tag)/='BEGIN' .or. rn<1 .or. rn>maxn) exit
      tp=0; tc=0; tr=.false.
      do j=1,rn
        read(u,*,iostat=ios) tag,idx,flag,tp(j),tc(1,j),tc(2,j); if(ios/=0) exit
        if(trim(tag)/='D' .or. idx/=j) ios=1
        tr(j)=flag==0
      end do
      if(ios/=0) exit
      read(u,*,iostat=ios) tag,est,eh; if(ios/=0) exit
      if(trim(tag)=='END' .and. est==st .and. eh==h .and. rn==n .and. st<=target .and. state_hash(rn,st,tp,tc,tr)==h) then
        found=.true.; bst=st; bt=t; p(1:n)=tp(1:n); c(:,1:n)=tc(:,1:n); r(1:n)=tr(1:n)
      end if
    end do
    close(u)
  end subroutine'''
new_load = '''  subroutine load_checkpoint(path,n,target,cfgid,bst,bt,p,c,r,found)
    character(*),intent(in)::path
    integer,intent(in)::n,target
    integer(int64),intent(in)::cfgid
    integer,intent(out)::bst
    real(real64),intent(out)::bt
    real(real64),intent(inout)::p(:),c(:,:)
    logical,intent(inout)::r(:)
    logical,intent(out)::found
    integer,parameter::maxkeep=20000
    integer::u,ios,row_ios,st,rn,j,idx,flag,est,keep_count,rec_count,last_keep,k
    integer(int64)::h,eh,ch
    real(real64)::t,tp(maxn),tc(2,maxn)
    logical::tr(maxn),record_ok
    character(len=16)::tag
    character(len=256)::line,rec(maxn+2),keep(maxkeep)
    found=.false.; bst=0; bt=0; keep_count=0; last_keep=-1
    open(newunit=u,file=path,status='old',action='read',iostat=ios)
    if(ios/=0) return
    do
      read(u,'(a)',iostat=ios) line
      if(ios/=0) exit
      rec_count=1; rec(1)=trim(line)
      tag=''
      read(line,*,iostat=row_ios) tag,st,rn,t,ch,h
      if(row_ios/=0 .or. trim(tag)/='BEGIN' .or. rn<1 .or. rn>maxn) cycle
      tp=0; tc=0; tr=.false.; record_ok=.true.
      do j=1,rn
        read(u,'(a)',iostat=ios) line
        if(ios/=0) exit
        rec_count=rec_count+1; rec(rec_count)=trim(line)
        read(line,*,iostat=row_ios) tag,idx,flag,tp(j),tc(1,j),tc(2,j)
        if(row_ios/=0) then
          record_ok=.false.
        else
          if(trim(tag)/='D' .or. idx/=j .or. (flag/=0 .and. flag/=1)) record_ok=.false.
          tr(j)=flag/=0
        end if
      end do
      if(ios/=0) exit
      read(u,'(a)',iostat=ios) line
      if(ios/=0) exit
      rec_count=rec_count+1; rec(rec_count)=trim(line)
      read(line,*,iostat=row_ios) tag,est,eh
      if(row_ios/=0 .or. trim(tag)/='END' .or. est/=st .or. eh/=h) record_ok=.false.
      if(record_ok .and. rn==n .and. st<=target .and. ch==cfgid .and. &
         state_hash(rn,st,tp,tc,tr)==h .and. st>last_keep) then
        if(keep_count+rec_count>maxkeep) error stop 'checkpoint history too large'
        do k=1,rec_count
          keep_count=keep_count+1; keep(keep_count)=rec(k)
        end do
        last_keep=st
        found=.true.; bst=st; bt=t; p(1:n)=tp(1:n); c(:,1:n)=tc(:,1:n); r(1:n)=tr(1:n)
      end if
    end do
    close(u)
    open(newunit=u,file=path,status='replace',action='write',iostat=ios)
    if(ios/=0) error stop 'cannot rewrite checkpoint'
    do k=1,keep_count
      write(u,'(a)') trim(keep(k))
    end do
    close(u)
  end subroutine'''
if old_load not in s:
    raise SystemExit('missing load_checkpoint block')
s = s.replace(old_load, new_load, 1)

p.write_text(s)
PY
make -C /app/amr clean all
