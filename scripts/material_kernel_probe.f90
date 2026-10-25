! SPDX-License-Identifier: GPL-2.0-only
! Finite differences of unmodified native small/finite-strain J2 material laws.
program oxygen_material_kernel_probe
  implicit none
  integer :: nonlinear, count, i,j, mi(1), thermal(1), nmethod, read_status
  real(8) :: parameters(21), curve(802), strain(6), initial(13,1,1), state(13,1,1)
  real(8) :: stress(6), stiffness(21), baseline(6), saved_state(13), saved_stiffness(21)
  real(8) :: plus(6),minus(6), difference(6,6), forward(6,6), backward(6,6), perturbed(6), dt,h, x,y
  character(80) :: name
  do
  read(*,*,iostat=read_status) nonlinear,parameters(1:2),count,dt,h
  if(read_status<0) exit
  if(read_status/=0) stop 5
  if ((nonlinear/=0.and.nonlinear/=1).or.count<1.or.count>200.or.dt<=0.or.h<=0) stop 2
  parameters(3:)=0;curve=0;mi=1;thermal=0;nmethod=1;name='OXYGEN SYNTHETIC J2 REPLAY'
  read(*,*) strain
  read(*,*) initial(:,1,1)
  do i=1,count
    read(*,*) x,y
    curve(2*i-1)=x;curve(2*i)=y
  enddo
  curve(801)=count
  call evaluate(strain,baseline)
  saved_state=state(:,1,1);saved_stiffness=stiffness
  do j=1,6
    x=h
    if(j>3) x=h/2
    perturbed=strain;perturbed(j)=perturbed(j)+x;call evaluate(perturbed,plus)
    perturbed=strain;perturbed(j)=perturbed(j)-x;call evaluate(perturbed,minus)
    difference(:,j)=(plus-minus)/(2*h)
    forward(:,j)=(plus-baseline)/h;backward(:,j)=(baseline-minus)/h
  enddo
  write(*,'(*(ES26.17E3,1X))') baseline
  write(*,'(*(ES26.17E3,1X))') saved_state
  write(*,'(*(ES26.17E3,1X))') saved_stiffness
  do i=1,6
    write(*,'(*(ES26.17E3,1X))') difference(i,:)
  enddo
  do i=1,6
    write(*,'(*(ES26.17E3,1X))') forward(i,:)
  enddo
  do i=1,6
    write(*,'(*(ES26.17E3,1X))') backward(i,:)
  enddo
  enddo
contains
  subroutine evaluate(e,value)
    real(8),intent(in) :: e(6)
    real(8),intent(out) :: value(6)
    real(8) :: beta(6),vJ,elastic(6),point(3),pnewdt,depvisc,c(3,3),det
    state=initial;stiffness=0;stress=0;beta=0;point=0;elastic=0;pnewdt=-1;depvisc=0;vJ=1
    if(nonlinear==1) then
      c=reshape((/1+2*e(1),2*e(4),2*e(5),2*e(4),1+2*e(2),2*e(6),2*e(5),2*e(6),1+2*e(3)/),(/3,3/))
      det=c(1,1)*(c(2,2)*c(3,3)-c(2,3)*c(3,2))-c(1,2)*(c(2,1)*c(3,3)-c(2,3)*c(3,1))+c(1,3)*(c(2,1)*c(3,2)-c(2,2)*c(3,1))
      if(det<=0) stop 3
      vJ=sqrt(det)
      call incplas(parameters,curve,state,initial,stiffness,e,thermal,0,beta,stress,vJ,-51,0,name,0.d0,dt,1.d0,0.d0,1,1,13,mi,elastic,point,nmethod,pnewdt,depvisc)
    else
      call incplas_lin(parameters,curve,state,initial,stiffness,e,thermal,0,beta,stress,vJ,-51,0,name,0.d0,dt,1.d0,0.d0,1,1,13,mi,elastic,point,nmethod,pnewdt,depvisc)
    endif
    if(pnewdt>0) stop 4
    value=stress
  end subroutine
end program
