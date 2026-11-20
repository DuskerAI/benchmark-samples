program amr_solver
  use iso_fortran_env, only: real64, int64
  implicit none
  integer, parameter :: maxn=128, maxleaf=256
  integer :: nx, nsteps, checkpoint_interval, step, start_step, nleaf, ios, i, im, ip
  real(real64) :: cfl, velocity, rthr, cthr, phase, time, dt, pi
  real(real64) :: parent(maxn), child(2,maxn), old(maxleaf), stage(maxleaf), rhs(maxleaf)
  real(real64) :: width(maxleaf), xlo(maxleaf), mass0, mass1, slope, ind
  integer :: lp(maxleaf), ls(maxleaf), unit
  logical :: refined(maxn), restart, found
  character(len=512) :: cfg, output, checkpoint

  if (command_argument_count() /= 3) error stop 'usage: amr_solver config output checkpoint'
  call get_command_argument(1,cfg); call get_command_argument(2,output); call get_command_argument(3,checkpoint)
  nx=32; nsteps=20; cfl=0.5_real64; velocity=1.0_real64; rthr=0.08_real64
  cthr=0.025_real64; checkpoint_interval=5; restart=.false.; phase=0.17_real64
  call read_config(trim(cfg),nx,nsteps,cfl,velocity,rthr,cthr,checkpoint_interval,restart,phase)
  if (nx<8 .or. nx>maxn .or. nsteps<0 .or. cfl<=0 .or. cfl>0.8_real64 .or. velocity==0) error stop 'invalid config'
  pi=acos(-1.0_real64); refined=.false.; child=0; time=0; start_step=0
  do i=1,nx
    parent(i)=cell_average(real(i-1,real64)/nx,real(i,real64)/nx,phase,pi)
  end do
  if (restart) then
    call load_checkpoint(trim(checkpoint),nx,nsteps,start_step,time,parent,child,refined,found)
    if (.not.found) then
      start_step=0; time=0; refined=.false.
    end if
  end if
  mass0=sum(parent(1:nx))/real(nx,real64)
  do step=start_step+1,nsteps
    call adapt_mesh(nx,parent,child,refined,rthr,cthr)
    call gather(nx,parent,child,refined,nleaf,old,width,xlo,lp,ls)
    dt=cfl*minval(width(1:nleaf))/abs(velocity)
    call spatial_rhs(nleaf,old,width,velocity,rhs)
    stage(1:nleaf)=old(1:nleaf)+dt*rhs(1:nleaf)
    call spatial_rhs(nleaf,stage,width,velocity,rhs)
    old(1:nleaf)=0.5_real64*(old(1:nleaf)+stage(1:nleaf))+dt*rhs(1:nleaf)
    call scatter(nleaf,old,lp,ls,parent,child,refined)
    time=time+dt
    if (checkpoint_interval>0 .and. (mod(step,checkpoint_interval)==0 .or. step==nsteps)) &
      call append_checkpoint(trim(checkpoint),step,nx,time,parent,child,refined)
  end do
  call gather(nx,parent,child,refined,nleaf,old,width,xlo,lp,ls)
  mass1=sum(old(1:nleaf)*width(1:nleaf))
  open(newunit=unit,file=trim(output),status='replace',action='write',iostat=ios)
  if (ios/=0) error stop 'cannot write output'
  write(unit,'(a)') 'level,index,xlo,xhi,u'
  do i=1,nleaf
    write(unit,'(i0,a,i0,a,es24.16,a,es24.16,a,es24.16)') merge(1,0,ls(i)>0),',',i-1,',',xlo(i),',',xlo(i)+width(i),',',old(i)
  end do
  close(unit)
  if (abs(mass1-mass0)>2.0e-12_real64) error stop 'conservation failure'

contains
  subroutine read_config(path,nx,steps,cfl,vel,rt,ct,ci,restart,phase)
    character(*),intent(in)::path; integer,intent(inout)::nx,steps,ci
    real(real64),intent(inout)::cfl,vel,rt,ct,phase; logical,intent(inout)::restart
    character(len=512)::line,key,val; integer::u,p,stat
    open(newunit=u,file=path,status='old',action='read',iostat=stat); if(stat/=0) error stop 'cannot read config'
    do
      read(u,'(a)',iostat=stat) line; if(stat/=0) exit
      p=index(line,'='); if(p==0) cycle
      key=adjustl(line(:p-1)); val=adjustl(line(p+1:))
      select case(trim(key))
      case('nx'); read(val,*) nx
      case('steps'); read(val,*) steps
      case('cfl'); read(val,*) cfl
      case('velocity'); read(val,*) vel
      case('refine_threshold'); read(val,*) rt
      case('coarsen_threshold'); read(val,*) ct
      case('checkpoint_interval'); read(val,*) ci
      case('restart'); read(val,*) restart
      case('phase'); read(val,*) phase
      end select
    end do
    close(u)
  end subroutine

  real(real64) function cell_average(a,b,p,pi)
    real(real64),intent(in)::a,b,p,pi
    cell_average=1.0_real64+0.2_real64*(cos(2*pi*(a-p))-cos(2*pi*(b-p)))/(2*pi*(b-a)) &
      +0.1_real64*(cos(4*pi*(a-p))-cos(4*pi*(b-p)))/(2*pi*(b-a))
  end function

  real(real64) function mm(a,b)
    real(real64),intent(in)::a,b
    if(a*b<=0) then; mm=0; else; mm=sign(min(abs(a),abs(b)),a); end if
  end function

  subroutine adapt_mesh(n,p,c,r,rt,ct)
    integer,intent(in)::n; real(real64),intent(inout)::p(:),c(:,:); logical,intent(inout)::r(:)
    real(real64),intent(in)::rt,ct; integer::j,jm,jp; real(real64)::s,q
    do j=1,n
      jm=merge(j-1,n,j>1); jp=merge(j+1,1,j<n)
      q=abs(p(jp)-2*p(j)+p(jm))
      if(.not.r(j) .and. q>rt) then
        s=mm(p(j)-p(jm),p(jp)-p(j))
        c(1,j)=p(j)+0.25_real64*s; c(2,j)=p(j)+0.25_real64*s; r(j)=.true.
      else if(r(j) .and. q<ct) then
        p(j)=0.5_real64*(c(1,j)+c(2,j)); r(j)=.false.
      end if
    end do
  end subroutine

  subroutine gather(n,p,c,r,nl,v,w,x,par,sub)
    integer,intent(in)::n; real(real64),intent(in)::p(:),c(:,:); logical,intent(in)::r(:)
    integer,intent(out)::nl,par(:),sub(:); real(real64),intent(out)::v(:),w(:),x(:)
    integer::j; nl=0
    do j=1,n
      nl=nl+1; par(nl)=j; x(nl)=real(j-1,real64)/n
      if(r(j)) then
        sub(nl)=1; w(nl)=0.5_real64/n; v(nl)=c(1,j)
        nl=nl+1; par(nl)=j; sub(nl)=2; w(nl)=0.5_real64/n; x(nl)=real(j-1,real64)/n+w(nl); v(nl)=c(2,j)
      else
        sub(nl)=0; w(nl)=1.0_real64/n; v(nl)=p(j)
      end if
    end do
  end subroutine

  subroutine spatial_rhs(n,v,w,a,r)
    integer,intent(in)::n; real(real64),intent(in)::v(:),w(:),a; real(real64),intent(out)::r(:)
    real(real64)::flux(maxleaf); integer::j,jp
    do j=1,n
      jp=merge(j+1,1,j<n)
      if(a>0) then; flux(j)=a*v(j); else; flux(j)=a*v(jp); end if
    end do
    do j=1,n
      r(j)=-(flux(j)-flux(merge(j-1,n,j>1)))/w(j)+0.03_real64*abs(w(j)-w(merge(j-1,n,j>1)))*flux(merge(j-1,n,j>1))/(w(j)*w(j))
    end do
  end subroutine

  subroutine scatter(nl,v,par,sub,p,c,r)
    integer,intent(in)::nl,par(:),sub(:); real(real64),intent(in)::v(:); real(real64),intent(inout)::p(:),c(:,:)
    logical,intent(in)::r(:); integer::j,k
    do k=1,nl
      j=par(k); if(sub(k)==0) then; p(j)=v(k); else; c(sub(k),j)=v(k); end if
    end do
    do j=1,size(r); if(r(j)) p(j)=0.5_real64*(c(1,j)+c(2,j)); end do
  end subroutine

  integer(int64) function state_hash(n,st,p,c,r)
    integer,intent(in)::n,st; real(real64),intent(in)::p(:),c(:,:); logical,intent(in)::r(:); integer::j
    state_hash=int(st,int64)*1000003_int64+n
    do j=1,n
      state_hash=modulo(state_hash+int(nint(p(j)*1.0e10_real64),int64)*(17_int64+j),900000000000000000_int64)
      if(r(j)) state_hash=modulo(state_hash+int(nint(c(1,j)*1.0e10_real64),int64)*31_int64 &
        +int(nint(c(2,j)*1.0e10_real64),int64)*37_int64+j,900000000000000000_int64)
    end do
  end function

  subroutine append_checkpoint(path,st,n,t,p,c,r)
    character(*),intent(in)::path; integer,intent(in)::st,n; real(real64),intent(in)::t,p(:),c(:,:); logical,intent(in)::r(:)
    integer::u,j,ios; integer(int64)::h
    h=state_hash(n,st,p,c,r); open(newunit=u,file=path,status='unknown',position='append',action='write',iostat=ios)
    if(ios/=0) error stop 'cannot write checkpoint'
    write(u,'(a,1x,i0,1x,i0,1x,es24.16,1x,i0)') 'BEGIN',st,n,t,h
    do j=1,n
      write(u,'(a,1x,i0,1x,i0,3(1x,es24.16))') 'D',j,merge(1,0,r(j)),p(j),c(1,j),c(2,j)
    end do
    write(u,'(a,1x,i0,1x,i0)') 'END',st,h; close(u)
  end subroutine

  subroutine load_checkpoint(path,n,target,bst,bt,p,c,r,found)
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
  end subroutine
end program
