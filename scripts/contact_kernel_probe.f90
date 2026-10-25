! SPDX-License-Identifier: GPL-2.0-only
! Verification driver for unmodified CalculiX 2.23 contact routines.
! This is a separate verification executable, not the application solver.
program contact_kernel_probe
  implicit none
  real(8) :: normal(3),jump(3),old(9),kn,kt,friction,area
  real(8) :: xl(3,19),vl(0:3,19),fnl(3,19),stiff(21),s(60,60)
  real(8) :: elcon(0:7,1,1),elconloc(82),plicon(0:2,1,1)
  real(8) :: state(9,4,1),initial(9,4,1),pslav(3,1),pmast(6,1)
  real(8) :: clearini(3,9,1),springarea(2),smscale(1),energy,work,cstr(6)
  real(8) :: t1(3),t2(3),uv(2,6),matrix(3,3),fd(3,3),plus(3),minus(3),h
  integer :: nelcon(2,1),nplicon(0:1,1),mi(3),konl(26),i,j,k,l
  character(8) :: lakon
  read(*,*) normal,jump,old,kn,kt,friction,area,h
  if(h.le.0.d0) stop 'Finite difference step must be positive'
  t1=[1.d0,0.d0,0.d0]
  if(abs(normal(1))>.9d0) t1=[0.d0,0.d0,1.d0]
  t1=t1-dot_product(t1,normal)*normal;t1=t1/sqrt(dot_product(t1,t1))
  t2=[normal(2)*t1(3)-normal(3)*t1(2),normal(3)*t1(1)-normal(1)*t1(3),normal(1)*t1(2)-normal(2)*t1(1)]
  uv(:,1)=[0.d0,0.d0];uv(:,2)=[1.d0,0.d0];uv(:,3)=[0.d0,1.d0]
  uv(:,4)=[.5d0,0.d0];uv(:,5)=[.5d0,.5d0];uv(:,6)=[0.d0,.5d0]
  xl=0.d0
  do i=1,6
    xl(:,i)=uv(1,i)*t1+uv(2,i)*t2;xl(:,i+6)=xl(:,i)
  enddo
  mi=[4,3,1];nelcon=0;nplicon=0;konl=0;elcon=0.d0;elconloc=0.d0;plicon=0.d0
  elcon(2,1,1)=kn;elcon(3,1,1)=2.d0;elcon(6,1,1)=friction;elcon(7,1,1)=kt
  clearini=0.d0;springarea=[area,0.d0];smscale=1.d0;lakon='ESPRNGC6'
  pslav(:,1)=[.2d0,.3d0,area];pmast(:,1)=[.2d0,.3d0,.5d0,normal]
  initial=0.d0;initial(:,1,1)=old
  call force(jump,plus)
  write(*,'(3ES26.17E3)') plus
  write(*,'(6ES26.17E3)') cstr
  write(*,'(9ES26.17E3)') state(:,1,1)
  vl=0.d0
  do i=7,12
    vl(1:3,i)=jump
  enddo
  state=initial;s=0.d0
  call springstiff_f2f(xl,stiff,vl,s,1,elcon,nelcon,7,1,12,lakon,0.d0,0,elconloc,plicon, &
    nplicon,1,[0,0],springarea,1,mi,0,9,initial,state,1.d0,1,1,1,pslav,pmast,clearini,1)
  matrix=0.d0
  do k=7,12
    do l=7,12
      matrix=matrix+s(3*(k-1)+1:3*k,3*(l-1)+1:3*l)
    enddo
  enddo
  do i=1,3
    write(*,'(3ES26.17E3)') matrix(i,:)
  enddo
  do j=1,3
    t1=jump;t1(j)=t1(j)+h;call force(t1,plus)
    t1=jump;t1(j)=t1(j)-h;call force(t1,minus)
    fd(:,j)=(plus-minus)/(2.d0*h)
  enddo
  do i=1,3
    write(*,'(3ES26.17E3)') fd(i,:)
  enddo
contains
  subroutine force(displacement,value)
    real(8),intent(in) :: displacement(3)
    real(8),intent(out) :: value(3)
    vl=0.d0
    do i=7,12
      vl(1:3,i)=displacement
    enddo
    fnl=0.d0;state=initial;energy=0.d0;work=0.d0;cstr=0.d0
    call springforc_f2f(xl,vl,1,elcon,nelcon,stiff,fnl,7,1,12,lakon,0.d0,0,elconloc, &
      plicon,nplicon,1,energy,1,cstr,mi,springarea,1,0,9,initial,state,1.d0,0,1,1, &
      pslav,pmast,clearini,work,1,konl,1,1,smscale,0)
    value=sum(fnl(:,7:12),dim=2)
  end subroutine
end program
